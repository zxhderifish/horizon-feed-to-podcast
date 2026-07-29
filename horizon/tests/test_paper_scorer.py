import asyncio
from datetime import datetime, timezone

from src.paper.scorer import TasteScorer
from src.models import ContentItem, SourceType


class FakeClient:
    """Returns a canned score based on the paper title."""
    def __init__(self, mapping):
        self.mapping = mapping

    async def complete(self, system, user, temperature=None, max_tokens=None):
        for title, score in self.mapping.items():
            if title in user:
                return f'{{"score": {score}, "reason": "test"}}'
        return '{"score": 0, "reason": "no match"}'


def _item(title):
    return ContentItem(
        id=f"arxiv:paper:{title}", source_type=SourceType.ARXIV, title=title,
        url="http://arxiv.org/abs/2507.00001", content="abstract",
        published_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )


def test_scorer_filters_and_sorts():
    items = [_item("on-topic FSDP"), _item("off-topic cooking"), _item("borderline")]
    client = FakeClient({"on-topic FSDP": 9, "off-topic cooking": 2, "borderline": 7})
    scorer = TasteScorer(client, threshold=7.0)
    kept = asyncio.run(scorer.score_and_filter(items))
    assert [i.title for i in kept] == ["on-topic FSDP", "borderline"]
    assert kept[0].ai_score == 9.0
    assert kept[0].ai_reason == "test"
