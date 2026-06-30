import logging
from datetime import datetime, timezone

from src.adapters.sources import ADAPTERS
from src.adapters.sources.x_list import XListAdapter
from src.core.types import Genre, Publisher, RunContext, SourceSpec


def _ctx():
    return RunContext(
        run_id="t",
        now=datetime(2026, 6, 30, 1, 0, tzinfo=timezone.utc),
        logger=logging.getLogger("test.x_list"),
    )


def _spec(list_id="L1", name="x-ai-lab", publisher=Publisher.lab, genre=Genre.announcement):
    return SourceSpec(
        name=name,
        url=f"xlist:{list_id}",
        genre=genre,
        publisher=publisher,
        adapter="x_list",
        status="manual",
    )


def test_x_list_is_registered_in_adapters():
    assert "x_list" in ADAPTERS
    assert isinstance(ADAPTERS["x_list"], XListAdapter)


async def test_x_list_empty_when_data_dir_has_no_files(tmp_path):
    adapter = XListAdapter(data_dir=tmp_path)
    items = await adapter.fetch(_spec(), _ctx(), timeout_s=15)
    assert items == []


def test_tweet_title_short_text_returned_as_is():
    from src.adapters.sources.x_list import _tweet_title

    assert _tweet_title("GPT-5 is here.") == "GPT-5 is here."


def test_tweet_title_first_line_only():
    from src.adapters.sources.x_list import _tweet_title

    text = "GPT-5 is here.\nDetails below.\nMore stuff."
    assert _tweet_title(text) == "GPT-5 is here."


def test_tweet_title_long_english_sentence_cuts_at_sentence_end():
    from src.adapters.sources.x_list import _tweet_title

    text = (
        "OpenAI just dropped GPT-5 with 10x reasoning improvements over GPT-4. "
        "This is huge and changes the entire landscape of foundation models forever."
    )
    out = _tweet_title(text, 140)
    assert out.endswith(".")
    assert len(out) <= 140
    assert "GPT-5" in out


def test_tweet_title_long_no_punct_cuts_at_word_boundary():
    from src.adapters.sources.x_list import _tweet_title

    text = "word " * 50  # 250 chars all words, no punct
    out = _tweet_title(text, 140)
    assert out.endswith("…")
    assert len(out) <= 140
    assert " " not in out[-2:]  # cut not mid-word


def test_tweet_title_long_chinese_no_punct_hard_cuts_with_ellipsis():
    from src.adapters.sources.x_list import _tweet_title

    text = "测" * 200  # 200 chars, no spaces, no sentence-end
    out = _tweet_title(text, 140)
    assert out.endswith("…")
    assert len(out) == 140  # n-1 chars + ellipsis = n


def test_tweet_title_emoji_and_url_preserved_when_short():
    from src.adapters.sources.x_list import _tweet_title

    text = "GPT-5 🔥 https://openai.com/gpt5"
    assert _tweet_title(text) == "GPT-5 🔥 https://openai.com/gpt5"


import shutil
from pathlib import Path

FIXTURE = Path(__file__).parent.parent / "fixtures" / "x_list_sample.ndjson"


def _seed_data(tmp_path, date_str="2026-06-30"):
    """复制 fixture 到 tmp_path/<date>.ndjson 模拟 extension PUT 的位置。"""
    dst = tmp_path / f"{date_str}.ndjson"
    shutil.copy(FIXTURE, dst)
    return dst


async def test_x_list_routes_by_list_id_and_maps_fields(tmp_path):
    _seed_data(tmp_path)
    adapter = XListAdapter(data_dir=tmp_path)
    spec = _spec(list_id="L1", name="x-ai-lab", publisher=Publisher.lab, genre=Genre.announcement)
    items = await adapter.fetch(spec, _ctx(), timeout_s=15)

    # L1 has 3 rows (1001, 1002, 1003); L2 + UNKNOWN excluded
    assert [it.link for it in items] == [
        "https://x.com/sama/status/1001",
        "https://x.com/demishassabis/status/1002",
        "https://x.com/swyx/status/1003",
    ]

    it = items[0]
    assert it.source == "x-ai-lab"
    assert it.title_en == "GPT-5 is here. 10x reasoning over GPT-4."
    assert it.genre == Genre.announcement
    assert it.publisher == Publisher.lab
    assert it.published_at.tzinfo is not None
    assert it.published_at == datetime(2026, 6, 30, 0, 30, tzinfo=timezone.utc)
    assert it.raw_summary.startswith("@sama:\n")
    assert "GPT-5 is here." in it.raw_summary
    assert it.signals == {
        "x_favorite": 12000,
        "x_retweet": 3400,
        "x_quote": 210,
        "x_reply": 890,
    }
    assert it.fetched_via == "native"


async def test_x_list_quote_tweet_appends_quoted_text_to_body(tmp_path):
    _seed_data(tmp_path)
    adapter = XListAdapter(data_dir=tmp_path)
    items = await adapter.fetch(_spec(list_id="L1"), _ctx(), timeout_s=15)
    # tweet 1003 has quoted_text
    quote_item = next(it for it in items if it.link.endswith("1003"))
    assert "@swyx:\n" in quote_item.raw_summary
    assert "Hot take on GPT-5" in quote_item.raw_summary
    assert "> 引用 @sama: GPT-5 is here." in quote_item.raw_summary


async def test_x_list_skips_rows_with_unknown_list_id(tmp_path, caplog):
    _seed_data(tmp_path)
    adapter = XListAdapter(data_dir=tmp_path)
    # spec L1 only — 1005 (list_id=UNKNOWN) and 1004 (list_id=L2) must not appear
    items = await adapter.fetch(_spec(list_id="L1"), _ctx(), timeout_s=15)
    links = [it.link for it in items]
    assert "https://x.com/rando/status/1005" not in links
    assert "https://x.com/ylecun/status/1004" not in links


async def test_x_list_different_spec_routes_different_rows(tmp_path):
    _seed_data(tmp_path)
    adapter = XListAdapter(data_dir=tmp_path)
    items = await adapter.fetch(
        _spec(
            list_id="L2", name="x-ai-kol-en", publisher=Publisher.individual, genre=Genre.writeup
        ),
        _ctx(),
        timeout_s=15,
    )
    assert len(items) == 1
    assert items[0].link.endswith("1004")
    assert items[0].source == "x-ai-kol-en"
    assert items[0].publisher == Publisher.individual
