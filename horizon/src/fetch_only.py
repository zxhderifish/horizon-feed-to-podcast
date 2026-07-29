"""Deterministic fetch-only path: fetch + cross-source URL dedup, no AI, no MCP."""

from typing import Any, List, Optional


async def collect_fetch_only(
    orchestrator: Any, force_hours: Optional[int] = None
) -> List[dict]:
    """Run only the deterministic stages and return JSON-ready dicts.

    Stages: time window -> fetch_all_sources -> merge_cross_source_duplicates.
    """
    since = orchestrator._determine_time_window(force_hours)
    raw_items = await orchestrator.fetch_all_sources(since)
    merged_items = orchestrator.merge_cross_source_duplicates(raw_items)
    return [item.model_dump(mode="json") for item in merged_items]
