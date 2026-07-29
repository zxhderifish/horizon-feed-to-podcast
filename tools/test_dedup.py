from datetime import datetime, timedelta, timezone

from dedup import filter_unseen, prune_ledger


def _now_iso(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def test_filter_unseen_drops_known_urls_and_returns_new_ledger_entries():
    items = [
        {"url": "https://a.com/1", "title": "A"},
        {"url": "https://b.com/2", "title": "B"},
    ]
    ledger = {"https://a.com/1": _now_iso(1)}

    fresh, new_entries = filter_unseen(items, ledger)

    assert [i["url"] for i in fresh] == ["https://b.com/2"]
    assert "https://b.com/2" in new_entries


def test_prune_ledger_drops_entries_older_than_window():
    ledger = {
        "https://old.com": _now_iso(10),
        "https://new.com": _now_iso(2),
    }
    pruned = prune_ledger(ledger, days=7)
    assert "https://new.com" in pruned
    assert "https://old.com" not in pruned
