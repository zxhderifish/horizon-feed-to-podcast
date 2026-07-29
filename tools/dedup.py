"""Deterministic seen-URL ledger: drop already-shipped items, prune old entries."""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple


def filter_unseen(
    items: List[dict], ledger: Dict[str, str]
) -> Tuple[List[dict], Dict[str, str]]:
    """Return (items not in ledger, ledger-entries to add for them)."""
    now = datetime.now(timezone.utc).isoformat()
    fresh = [i for i in items if i.get("url") not in ledger]
    new_entries = {i["url"]: now for i in fresh if i.get("url")}
    return fresh, new_entries


def prune_ledger(ledger: Dict[str, str], days: int = 7) -> Dict[str, str]:
    """Drop ledger entries older than `days`."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = {}
    for url, ts in ledger.items():
        try:
            seen_at = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if seen_at >= cutoff:
            out[url] = ts
    return out
