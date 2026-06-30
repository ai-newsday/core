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
