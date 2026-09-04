"""HTTP-level verification of the dashboard (no browser dependency).

Run: python -m tests.verify_dashboard
"""
import sys

import requests


def main() -> int:
    base = "http://localhost:8000"

    r = requests.get(f"{base}/", timeout=10)
    assert r.status_code == 200, f"GET / -> {r.status_code}"
    html = r.text
    for needle in [
        "Robot Trading Options",
        "Nilai Akun",
        "Cara Sistem Berpikir",
        "Pemeriksaan risiko",
        "Riwayat Posisi Selesai",
        "mode kertas (paper)",
    ]:
        assert needle in html, f"missing in HTML: {needle!r}"

    s = requests.get(f"{base}/api/state", timeout=20).json()
    acct = s.get("account", {})
    print("dashboard HTML   : OK (semua elemen kunci ada)")
    print(f"market_open      : {acct.get('market_open')}")
    print(f"equity           : ${acct.get('equity')}")
    print(f"open_positions   : {len(s.get('open_positions', []))}")
    lc = s.get("latest_cycle") or {}
    results = lc.get("results") or []
    acts = [x.get("action", x.get("error", "?")) for x in results]
    print(f"latest_cycle     : {lc.get('cycle_id')} actions={acts}")
    assert isinstance(acct.get("equity"), (int, float)), "equity harus angka"
    print("\nDashboard verified via HTTP.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
