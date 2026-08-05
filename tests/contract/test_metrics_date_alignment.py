"""Regression: report/metrics date must be *today* in the configured local timezone.

2026-08-01: user relocated to the UK and finalize moved from "next morning,
labelled yesterday" to "same day 23:00 local, labelled today". Both halves
matter — the timezone is no longer Asia/Shanghai, and the date is no longer
yesterday. These tests pin the new semantics so neither silently reverts.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def test_report_date_is_today_in_configured_tz_not_yesterday():
    from src.cli import _report_date

    tz = "Europe/London"
    today_local = datetime.now(ZoneInfo(tz)).date().isoformat()
    assert _report_date(tz) == today_local
    yesterday_local = (datetime.now(ZoneInfo(tz)).date() - timedelta(days=1)).isoformat()
    assert _report_date(tz) != yesterday_local


def test_report_date_honours_the_timezone_it_is_given():
    """Not hardcoded: a different tz must be able to yield a different date.
    Pacific/Kiritimati (UTC+14) and Pacific/Niue (UTC-11) are 25h apart, so at
    any instant their local dates differ — proving the argument is really used."""
    from src.cli import _report_date

    assert _report_date("Pacific/Kiritimati") != _report_date("Pacific/Niue")


def test_report_date_defaults_to_configured_publish_timezone():
    """Called with no argument it must fall back to PublishConfig's timezone,
    so metrics and the published post always agree on the date."""
    from src.cli import _report_date
    from src.core.types import PublishConfig

    expected = datetime.now(ZoneInfo(PublishConfig().timezone)).date().isoformat()
    assert _report_date() == expected


def test_publish_config_timezone_defaults_to_uk():
    from src.core.types import PublishConfig

    assert PublishConfig().timezone == "Europe/London"


def test_load_publish_config_reads_timezone(tmp_path):
    from src.core.config import load_publish_config

    p = tmp_path / "publish.yaml"
    p.write_text('timezone: "Asia/Tokyo"\n', encoding="utf-8")
    assert load_publish_config(str(p)).timezone == "Asia/Tokyo"


def test_production_publish_yaml_has_a_valid_timezone():
    from src.core.config import load_publish_config

    tz = load_publish_config("config/publish.yaml").timezone
    ZoneInfo(tz)  # raises if the shipped value isn't a real zone
