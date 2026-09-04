from datetime import date

from data_engine.news_data import estimate_earnings_proximity


def test_earnings_estimate_keeps_next_quarter_month_across_year_boundary():
    result = estimate_earnings_proximity("SPY", today=date(2026, 9, 2))

    assert result["estimated_next_earnings"] == "2027-01-15"
    assert result["earnings_proximity_days"] == 135
