import json
from pathlib import Path

from src.models import Config

CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "config.json"


def test_config_parses_with_market_rss_sources():
    raw = json.loads(CONFIG_PATH.read_text())
    config = Config.model_validate(raw)

    market_feeds = [
        s for s in config.sources.rss if (s.category or "") == "market"
    ]
    # At least the Fed feed plus one working tech/markets feed.
    assert len(market_feeds) >= 2, "expected >=2 RSS feeds with category 'market'"
    assert all(s.enabled for s in market_feeds), "market feeds must be enabled"
    assert all(str(s.url).startswith("http") for s in market_feeds)
