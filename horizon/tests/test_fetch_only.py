import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from rich.console import Console

from src.fetch_only import collect_fetch_only
from src.models import ContentItem, FilteringConfig, SourceType
from src.orchestrator import HorizonOrchestrator


def _make_orchestrator(items):
    orch = HorizonOrchestrator.__new__(HorizonOrchestrator)
    orch.config = SimpleNamespace(filtering=FilteringConfig())
    orch.console = Console(record=True)

    async def fake_fetch(since):
        return items

    orch.fetch_all_sources = fake_fetch
    orch.merge_cross_source_duplicates = lambda xs: xs
    return orch


def test_collect_fetch_only_returns_json_serializable_dicts():
    item = ContentItem(
        id="hackernews:story:1",
        source_type=SourceType.HACKERNEWS,
        title="Test story",
        url="https://example.com/1",
        published_at=datetime.now(timezone.utc),
    )
    orch = _make_orchestrator([item])

    result = asyncio.run(collect_fetch_only(orch, force_hours=24))

    assert isinstance(result, list)
    json.dumps(result)  # must not raise
    assert result[0]["id"] == "hackernews:story:1"
    assert result[0]["source_type"] == "hackernews"
    assert result[0]["ai_score"] is None
