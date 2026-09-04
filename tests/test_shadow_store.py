from execution import shadow_store


def test_shadow_store_is_idempotent_and_resolves_net_option_pnl(monkeypatch, tmp_path):
    monkeypatch.setattr("config.OPERATIONAL_DB_PATH", tmp_path / "ops.db")
    observation = {
        "observation_id": "obs-1",
        "underlying": "AAPL",
        "contract_symbol": "AAPL260919C00200000",
        "direction": "BULLISH",
        "volatility_regime": "LOW",
        "dte": 10,
        "delta": 0.55,
        "entry_bar_count": 100,
        "entry_timestamp": "2026-09-01T14:00:00+00:00",
        "entry_ask": 2.10,
        "entry_bid": 2.00,
        "horizon_bars": 5,
    }

    assert shadow_store.record_observation(observation) is True
    assert shadow_store.record_observation(observation) is False
    shadow_store.resolve_observations(
        "AAPL",
        current_bar_count=105,
        current_timestamp="2026-09-08T14:00:00+00:00",
        quotes={"AAPL260919C00200000": {"bid": 2.75}},
    )

    outcomes = shadow_store.resolved_outcomes()
    assert len(outcomes) == 1
    assert outcomes[0]["net_pnl_usd"] == 65.0
    assert outcomes[0]["profitable"] is True


def test_process_snapshot_records_valid_contracts_only(monkeypatch, tmp_path):
    monkeypatch.setattr("config.OPERATIONAL_DB_PATH", tmp_path / "ops.db")
    result = shadow_store.process_snapshot(
        "AAPL",
        {"bar_count": 100, "bar_timestamp": "2026-09-01"},
        [{
            "symbol": "AAPL260919C00200000",
            "direction": "BULLISH",
            "bid": 2.0,
            "ask": 2.1,
            "dte": 10,
            "delta": 0.55,
            "contract_confidence": {"volatility_regime": "LOW"},
            "profitability": {"valid": True},
        }, {
            "symbol": "AAPL260919C00210000",
            "direction": "BULLISH",
            "bid": 1.0,
            "ask": 1.1,
            "dte": 10,
            "delta": 0.55,
            "profitability": {"valid": False},
        }],
    )

    assert result["recorded"] == 1
    assert len(shadow_store.pending_observations("AAPL")) == 1


def test_new_observations_use_the_active_calibration_version(monkeypatch, tmp_path):
    monkeypatch.setattr("config.OPERATIONAL_DB_PATH", tmp_path / "ops.db")
    observation = {
        "observation_id": "versioned",
        "underlying": "AAPL",
        "contract_symbol": "AAPL260919C00200000",
        "direction": "BULLISH",
        "dte": 10,
        "delta": 0.55,
        "entry_bar_count": 100,
        "entry_timestamp": "2026-09-01T14:00:00+00:00",
        "entry_ask": 2.10,
        "horizon_bars": 1,
    }

    assert shadow_store.record_observation(observation) is True
    row = shadow_store.pending_observations("AAPL")[0]

    assert row["calibration_version"] == shadow_store.CALIBRATION_VERSION


def test_legacy_calibration_rows_are_archived_and_excluded(monkeypatch, tmp_path):
    monkeypatch.setattr("config.OPERATIONAL_DB_PATH", tmp_path / "ops.db")
    observation = {
        "observation_id": "legacy",
        "underlying": "AAPL",
        "contract_symbol": "AAPL260919C00200000",
        "direction": "BULLISH",
        "dte": 10,
        "delta": 0.55,
        "entry_bar_count": 100,
        "entry_timestamp": "2026-09-01T14:00:00+00:00",
        "entry_ask": 2.10,
        "horizon_bars": 5,
    }
    assert shadow_store.record_observation(observation) is True
    with shadow_store._connection() as conn:
        conn.execute(
            "UPDATE shadow_observations SET calibration_version = 'legacy-v1' "
            "WHERE observation_id = 'legacy'"
        )

    assert shadow_store.pending_observations("AAPL") == []
    assert shadow_store.archived_observation_count() == 1


def test_bar_count_uses_weekday_ordinal_and_legacy_rows_use_entry_date():
    assert shadow_store.bar_count_from_timestamp("2026-09-04") + 1 == shadow_store.bar_count_from_timestamp("2026-09-07")
    assert shadow_store._stored_bar_count({
        "entry_bar_count": 277,
        "entry_timestamp": "2026-09-01T14:00:00+00:00",
        "timeframe": "1D",
    }) == shadow_store.bar_count_from_timestamp("2026-09-01")


def test_daily_and_hourly_shadow_observations_resolve_independently(monkeypatch, tmp_path):
    monkeypatch.setattr("config.OPERATIONAL_DB_PATH", tmp_path / "ops.db")
    base = {
        "underlying": "AAPL",
        "contract_symbol": "AAPL260919C00200000",
        "direction": "BULLISH",
        "volatility_regime": "LOW",
        "dte": 10,
        "delta": 0.55,
        "entry_timestamp": "2026-09-01T14:00:00+00:00",
        "entry_ask": 2.10,
        "entry_bid": 2.00,
        "horizon_bars": 5,
    }
    daily = {**base, "observation_id": "daily", "entry_bar_count": 14783, "timeframe": "1D"}
    hourly = {**base, "observation_id": "hourly", "entry_bar_count": 103481, "timeframe": "1H"}
    assert shadow_store.record_observation(daily) is True
    assert shadow_store.record_observation(hourly) is True

    assert shadow_store.resolve_observations(
        "AAPL", current_bar_count=103486, timeframe="1H",
        quotes={"AAPL260919C00200000": {"bid": 2.75}},
    ) == 1

    assert [row["timeframe"] for row in shadow_store.pending_observations("AAPL")] == ["1D"]
    assert [row["timeframe"] for row in shadow_store.resolved_outcomes("AAPL")] == ["1H"]
