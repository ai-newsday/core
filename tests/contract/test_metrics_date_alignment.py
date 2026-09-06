"""报告与 metrics 的日期 = **读者读到它的那一天**(spec: 发布日命名)。

沿革:
- 2026-08-01 用户搬到英国, 结算从"次日早上、标昨天"改成"当天 23:00 本地、标当天"。
- 2026-09-05 再改成"标发布日": 用户在伦敦午夜之后发布, 对应北京时间早上八点左右,
  所以晚上跑出来的那份属于**第二天**的刊物。

这不只是命名偏好。2026-09-04 两次 finalize 落在同一个伦敦日(00:04 与 23:52), 拿到
同一个 `date_label`, 于是跨天去重的"同 label 视为重跑"豁免把两条已发布条目
(K2-Horizon-MoVA / Runway GWM Worlds 2, **链接完全相同**)整个放行, 连发两天, 并且
第二次覆盖了已经发布出去的那份文件。按发布日命名之后这两次自然落在不同 label 上。

时钟一律注入: 原来的测试用 `datetime.now()`, 结果本身依赖跑测试的时刻——傍晚之后
跑就会飘。
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = "Europe/London"


def _utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=ZoneInfo("UTC"))


def test_evening_run_is_labelled_the_next_day():
    """真实事故的后半段: 09-04 22:52 UTC = 伦敦 23:52, 用户在午夜后发布 -> 09-05。"""
    from src.cli import _report_date

    assert _report_date(TZ, now=_utc("2026-09-04T22:52")) == "2026-09-05"


def test_after_midnight_run_is_labelled_that_same_day():
    """真实事故的前半段: 09-03 23:04 UTC = 伦敦 09-04 00:04, 已经过了午夜,
    这次跑出来的就是当天要发的那份 -> 09-04。"""
    from src.cli import _report_date

    assert _report_date(TZ, now=_utc("2026-09-03T23:04")) == "2026-09-04"


def test_the_two_real_runs_no_longer_collide_on_one_label():
    """回归 (#160): 这两次真实运行原本拿到同一个 label, 跨天去重因此被架空,
    K2 与 GWM 连发两天。它们必须落在不同的 label 上。"""
    from src.cli import _report_date

    a = _report_date(TZ, now=_utc("2026-09-03T23:04"))
    b = _report_date(TZ, now=_utc("2026-09-04T22:52"))
    assert a != b, "两次运行仍然撞在同一个 label 上, 重复条目会再次发生"


def test_morning_rerun_keeps_the_same_label_as_the_night_before():
    """同一份刊物的重跑必须拿到同一个 label, 否则重跑会被当成新的一天、
    把昨晚已发的条目全部重新放行。"""
    from src.cli import _report_date

    night = _report_date(TZ, now=_utc("2026-09-04T22:52"))  # 伦敦 23:52
    morning = _report_date(TZ, now=_utc("2026-09-05T09:00"))  # 伦敦 10:00 次日
    assert night == morning == "2026-09-05"


def test_label_is_not_yesterday():
    from src.cli import _report_date

    now = _utc("2026-09-04T12:00")
    assert _report_date(TZ, now=now) != (now.date() - timedelta(days=1)).isoformat()


def test_report_date_honours_the_timezone_it_is_given():
    """Not hardcoded: a different tz must be able to yield a different date.
    Pacific/Kiritimati (UTC+14) and Pacific/Niue (UTC-11) are 25h apart, so at
    any instant their local dates differ — proving the argument is really used."""
    from src.cli import _report_date

    assert _report_date("Pacific/Kiritimati") != _report_date("Pacific/Niue")


def test_report_date_defaults_to_configured_publish_timezone():
    """不传参数时回落到 PublishConfig 的时区, 保证 metrics 与正刊日期一致。"""
    from src.cli import _report_date
    from src.core.types import PublishConfig

    now = _utc("2026-09-04T12:00")
    expected = _report_date(PublishConfig().timezone, now=now)
    assert _report_date(now=now) == expected


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
