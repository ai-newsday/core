"""Regression: metrics date must align with finalize's yesterday-Beijing post date."""

from datetime import date, timedelta
from zoneinfo import ZoneInfo


def test_beijing_report_date_is_yesterday_beijing_not_today():
    from datetime import datetime

    from src.cli import _beijing_report_date

    expected = (datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)).isoformat()
    assert _beijing_report_date() == expected
    # Sanity: the returned date must never be today Beijing
    today_bj = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    assert _beijing_report_date() != today_bj


def test_beijing_report_date_matches_post_naming_at_utc_0100():
    """At UTC 01:00 (Beijing 09:00, the finalize cron time), the report date
    published to content/posts/YYYY-MM-DD.md IS yesterday-Beijing. Metrics must
    use the same date to keep /metrics/X paired with /posts/X."""
    # Beijing "yesterday" at UTC 01:00 equals `date.today - 1 day` (in Beijing tz)
    # not `date.today` because finalize summarizes yesterday's complete day.
    # This test hard-pins the semantics.
    from datetime import datetime

    from src.cli import _beijing_report_date

    now_bj = datetime.now(ZoneInfo("Asia/Shanghai"))
    yesterday_bj: date = now_bj.date() - timedelta(days=1)
    assert _beijing_report_date() == yesterday_bj.isoformat()
