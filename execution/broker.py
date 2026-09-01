"""
execution/broker.py — Alpaca broker abstraction.

Design: abstract interface + concrete implementation.
The abstract class is the contract; the concrete class wraps alpaca-py.
Both live here so the interface types are always co-located with the implementation.

ARCHITECTURE (PRD §5.4 / CLAUDE.md):
  Only deterministic execution services may hold broker client instances.
  LLM agents have no broker client and no trading credentials.
  The orchestrator holds one broker instance per account, scoped by alpaca_account_id.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alpaca.trading.requests import OrderRequest

logger = logging.getLogger("aeroquant.broker")

SCHEMA_VERSION = "2.6.0"


class BrokerSnapshotUnavailable(RuntimeError):
    """A broker position snapshot could not be obtained; callers must fail closed."""


class BrokerLookupError(RuntimeError):
    """Client-order-ID lookup failed for a reason other than explicit not-found."""


# ---------------------------------------------------------------------------
# Types shared with force_close_guard.py / scheduler
# ---------------------------------------------------------------------------

class OrderStatus(str, Enum):
    NEW = "new"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PENDING_NEW = "pending_new"
    PENDING_CANCEL = "pending_cancel"
    PENDING_REPLACE = "pending_replace"
    REJECTED = "rejected"
    SUSPENDED = "suspended"


@dataclass
class BrokerPosition:
    """A position returned by the broker. Normalized from Alpaca's format."""

    symbol: str
    quantity: int          # signed: positive = long, negative = short
    side: str              # "long" | "short"
    market_value: float
    unrealized_pl: float
    expiration: datetime | None = None
    strike: float | None = None
    position_type: str | None = None  # "call" | "put" for OCC option symbols


@dataclass
class BrokerOrder:
    """An order returned by the broker. Normalized from Alpaca's format."""

    order_id: str
    status: OrderStatus
    symbol: str | None
    filled_qty: int
    filled_avg_price: float | None
    created_at: datetime
    updated_at: datetime
    # For MLEG: legs are tracked individually via the broker's order legs field
    legs: list[dict] | None = None


@dataclass
class AccountSnapshot:
    """Current account state from the broker."""

    buying_power: float
    cash: float
    portfolio_value: float
    equity: float
    alpaca_account_id: str
    timestamp: datetime


class AlpacaBroker(ABC):
    """
    Abstract broker interface.

    All concrete implementations must be drop-in swappable for testing.
    The execution layer holds one AlpacaBroker instance per account.
    """

    @abstractmethod
    def get_account(self) -> AccountSnapshot:
        """Fetch current account state. Used by the orchestrator to build TradeProposal context."""
        ...

    @abstractmethod
    def get_positions(self) -> list[BrokerPosition]:
        """Return all open positions, or raise BrokerSnapshotUnavailable."""
        ...

    @abstractmethod
    def get_order(self, order_id: str) -> BrokerOrder | None:
        """Return one order by ID, or None if not found."""
        ...

    @abstractmethod
    def submit_order(
        self,
        order_data: "OrderRequest",
        idempotency_key: str,
    ) -> str:
        """Submit an SDK order request and return the broker-confirmed order ID."""
        ...

    @abstractmethod
    def find_order_by_client_order_id(self, client_order_id: str) -> str | None:
        """Return the broker-confirmed order ID for this account-scoped client ID."""
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> None:
        """Cancel a specific order. Idempotent (404 = already gone = ok."""
        ...

    @abstractmethod
    def reconcile(self) -> dict:
        """
        Reconcile broker state after restart.

        Returns dict with keys:
          positions: list[BrokerPosition]
          open_order_ids: list[str]
        Used to rebuild ForceCloseGuard._claimed_legs and position state.
        """
        ...


    @property
    @abstractmethod
    def account_id(self) -> str:
        """Alpaca account ID for this broker instance."""
        ...

    @abstractmethod
    def is_market_open(self) -> bool:
        """True if market is currently open (RTH or GTH). Used to gate trading."""
        ...


class AlpacaConcreteBroker(AlpacaBroker):
    """
    Concrete Alpaca Paper Trading implementation.

    Injected at construction — never imports .env directly.
    Calling code provides already-validated credentials via constructor args.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str,
        account_id: str,
    ) -> None:
        self._account_id = account_id
        self._sdk_client = None  # lazily initialized
        self._key = api_key
        self._secret = api_secret
        self._base_url = base_url
        self._log = logging.getLogger("aeroquant.broker.alpaca")

    @property
    def account_id(self) -> str:
        return self._account_id

    def _client(self):
        """Lazily create the alpaca-py TradingClient. Import here so tests can mock the module."""
        if self._sdk_client is None:
            from alpaca.trading.client import TradingClient
            self._sdk_client = TradingClient(
                api_key=self._key,
                secret_key=self._secret,
                paper=True,
                url_override=self._base_url,
            )
        return self._sdk_client

    def get_account(self) -> AccountSnapshot:
        client = self._client()
        acct = client.get_account()
        from datetime import datetime as dt, timezone as tz
        return AccountSnapshot(
            buying_power=float(acct.buying_power or "0"),
            cash=float(acct.cash or "0"),
            portfolio_value=float(acct.portfolio_value or "0"),
            equity=float(acct.equity or "0"),
            alpaca_account_id=acct.id or self._account_id,
            timestamp=dt.now(tz.utc),
        )

    def get_positions(self) -> list[BrokerPosition]:
        client = self._client()
        try:
            raw_positions = client.get_all_positions()
        except Exception as exc:  # noqa: BLE001
            raise BrokerSnapshotUnavailable("broker position snapshot unavailable") from exc

        positions = []
        for position in raw_positions:
            raw_qty = int(Decimal(str(position.qty or "0")))
            side = getattr(position.side, "value", position.side).lower()
            quantity = -abs(raw_qty) if side == "short" else abs(raw_qty)
            expiration, strike, position_type = _parse_occ_option_symbol(position.symbol)
            positions.append(BrokerPosition(
                symbol=position.symbol,
                quantity=quantity,
                side=side,
                market_value=float(position.market_value or "0"),
                unrealized_pl=float(position.unrealized_pl or "0"),
                expiration=expiration,
                strike=strike,
                position_type=position_type,
            ))
        return positions

    def get_order(self, order_id: str) -> BrokerOrder | None:
        client = self._client()
        try:
            raw = client.get_order_by_id(order_id)
        except Exception:  # noqa: BLE001
            return None
        return BrokerOrder(
            order_id=raw.id,
            status=OrderStatus(raw.status.value),
            symbol=raw.symbol,
            filled_qty=int(raw.filled_qty or "0"),
            filled_avg_price=float(raw.filled_avg_price or "0"),
            created_at=raw.created_at,
            updated_at=raw.updated_at,
            legs=None,
        )

    def submit_order(
        self,
        order_data: "OrderRequest",
        idempotency_key: str,
    ) -> str:
        """Submit a typed alpaca-py request with its account-scoped client ID."""
        client = self._client()
        request = order_data.model_copy(
            update={"client_order_id": idempotency_key}
        )
        self._log.info(
            "broker_submit order_idempotency_key=%s order_type=%s order_class=%s",
            idempotency_key, request.type, request.order_class,
        )
        response = client.submit_order(order_data=request)
        self._log.info("broker_submitted order_id=%s", response.id)
        return str(response.id)

    def find_order_by_client_order_id(self, client_order_id: str) -> str | None:
        """Look up an account-scoped Alpaca client order ID without failing open."""
        try:
            order = self._client().get_order_by_client_id(client_id=client_order_id)
        except Exception as exc:  # noqa: BLE001
            if _http_status_code(exc) == 404:
                return None
            raise BrokerLookupError("broker client-order-ID lookup failed") from exc
        return str(order.id)

    def cancel_order(self, order_id: str) -> None:
        client = self._client()
        try:
            client.cancel_order(order_id)
            self._log.info("broker_cancel order_id=%s", order_id)
        except Exception:  # noqa: BLE001 — 404 = already gone = fine
            self._log.warning("broker_cancel_404 order_id=%s", order_id)

    def reconcile(self) -> dict:
        """Re-build position + open-order snapshot for ForceCloseGuard state rebuild."""
        positions = self.get_positions()
        client = self._client()
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            open_orders = client.get_orders(
                filter=GetOrdersRequest(
                    status=QueryOrderStatus.OPEN,
                    limit=100,
                )
            )
        except Exception:  # noqa: BLE001
            # Position state remains usable, but order state is unknown. Do not
            # pretend tracked close orders disappeared.
            open_orders = None

        open_ids = None if open_orders is None else [str(order.id) for order in open_orders]
        self._log.info(
            "broker_reconcile positions=%d open_orders=%s", len(positions),
            "unavailable" if open_ids is None else len(open_ids),
        )
        return {
            "positions": positions,
            "open_order_ids": open_ids,
        }

    def is_market_open(self) -> bool:
        """True if market is currently open (RTH or GTH). Used to gate trading."""
        from alpaca.trading.client import TradingClient
        try:
            client = self._client()
            clock = client.get_clock()
            return clock.is_open
        except Exception:  # noqa: BLE001
            self._log.warning("broker_clock_unavailable default_closed")
            return False


def _parse_occ_option_symbol(symbol: str) -> tuple[datetime | None, float | None, str | None]:
    """Parse Alpaca's OCC option symbol into scheduler metadata when applicable."""
    match = re.fullmatch(r"[A-Z]+(\d{6})([CP])(\d{8})", symbol)
    if match is None:
        return None, None, None
    expiry = datetime.strptime(match.group(1), "%y%m%d").replace(tzinfo=timezone.utc)
    position_type = "call" if match.group(2) == "C" else "put"
    strike = int(match.group(3)) / 1000
    return expiry, strike, position_type


def _http_status_code(exc: Exception) -> int | None:
    """Read an SDK/transport status code without relying on a concrete exception."""
    try:
        status = getattr(exc, "status_code", None)
    except Exception:  # noqa: BLE001
        return None
    return status if isinstance(status, int) else None
