import asyncio
from datetime import datetime, timezone

from src.paper.insight import PaperInsighter, INSIGHT_KEYS
from src.models import ContentItem, SourceType


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
    async def complete(self, system, user, temperature=None, max_tokens=None):
        return self.payload


def _item():
    return ContentItem(
        id="arxiv:paper:2507.00001", source_type=SourceType.ARXIV,
        title="FSDP2 memory study", url="http://arxiv.org/abs/2507.00001",
        content="abstract", published_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
        ai_score=8.0, ai_reason="on topic",
    )


def test_insight_populates_all_keys_bilingual():
    payload = (
        '{"open_source": {"en": "yes, github.com/x", "zh": "有, github.com/x"},'
        ' "task": {"en": "memory opt", "zh": "内存优化"},'
        ' "metrics": {"en": "peak mem / MFU", "zh": "峰值显存 / MFU"},'
        ' "improvement": {"en": "-30% mem", "zh": "省 30% 显存"},'
        ' "core_idea": {"en": "reshard", "zh": "分片再分片"},'
        ' "mental_model": {"en": "confirms ZeRO tiers", "zh": "印证了 ZeRO 分层"},'
        ' "reachable": {"en": "2-GPU reachable", "zh": "双卡够得着"},'
        ' "confidence": {"en": "medium", "zh": "中"},'
        ' "needs_web_check": true}'
    )
    insighter = PaperInsighter(FakeClient(payload))
    item = _item()
    asyncio.run(insighter.annotate([item]))
    ins = item.metadata["insight"]
    for key in INSIGHT_KEYS:
        assert key in ins
    assert ins["core_idea"]["zh"] == "分片再分片"
    assert ins["core_idea"]["en"] == "reshard"
    assert ins["needs_web_check"] is True


def test_insight_survives_bad_json():
    insighter = PaperInsighter(FakeClient("not json"))
    item = _item()
    asyncio.run(insighter.annotate([item]))
    assert item.metadata["insight"]["core_idea"] == {"en": "", "zh": ""}
    assert item.metadata["insight"]["needs_web_check"] is False
