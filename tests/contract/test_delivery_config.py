from src.core.config import load_delivery_config
from src.core.types import DeliveryConfig


def test_load_delivery_config_missing_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    cfg = load_delivery_config(str(tmp_path / "nope.yaml"))
    assert cfg == DeliveryConfig()


def test_load_delivery_config_telegram_fields(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    p = tmp_path / "delivery.yaml"
    p.write_text("telegram:\n  mode: webhook\n  webhook_url: https://x.com\n", encoding="utf-8")
    cfg = load_delivery_config(str(p))
    assert cfg.telegram.mode == "webhook"
    assert cfg.telegram.webhook_url == "https://x.com"
    assert cfg.telegram.bot_token == ""


def test_load_delivery_config_website_fields(tmp_path):
    p = tmp_path / "delivery.yaml"
    p.write_text(
        "website:\n  enabled: true\n  output_dir: out\n  git_push: true\n", encoding="utf-8"
    )
    cfg = load_delivery_config(str(p))
    assert cfg.website.enabled is True
    assert cfg.website.output_dir == "out"
    assert cfg.website.git_push is True


def test_website_default_output_dir_is_content_posts(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    cfg = load_delivery_config("does/not/exist.yaml")
    assert cfg.website.output_dir == "content/posts"


def test_telegram_config_reads_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "99")
    from src.core.config import load_delivery_config

    cfg = load_delivery_config("nonexistent.yaml")
    assert cfg.telegram.bot_token == "tok123"
    assert cfg.telegram.chat_id == "99"
