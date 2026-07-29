import asyncio
from datetime import datetime, timezone

from src.paper.synthesis import WeeklySynthesizer
from src.models import ContentItem, SourceType


class FakeClient:
    def __init__(self, text):
        self.text = text
        self.last_user = None
    async def complete(self, system, user, temperature=None, max_tokens=None):
        self.last_user = user
        return self.text


def _item(title):
    return ContentItem(
        id=f"arxiv:paper:{title}", source_type=SourceType.ARXIV, title=title,
        url="http://arxiv.org/abs/2507.00001", content="abstract",
        published_at=datetime(2026, 7, 3, tzinfo=timezone.utc), ai_score=8.0,
    )


def test_synthesis_returns_bilingual_and_sees_titles():
    client = FakeClient('{"en": "Three papers on overlap.", "zh": "这周三篇都在讲通信重叠。"}')
    synth = WeeklySynthesizer(client)
    items = [_item("paper A"), _item("paper B")]
    out = asyncio.run(synth.synthesize(items))
    assert out == {"en": "Three papers on overlap.", "zh": "这周三篇都在讲通信重叠。"}
    assert "paper A" in client.last_user and "paper B" in client.last_user


def test_synthesis_empty_batch():
    synth = WeeklySynthesizer(FakeClient("unused"))
    out = asyncio.run(synth.synthesize([]))
    assert "本周无" in out["zh"]
    assert out["en"]  # non-empty English fallback


def test_synthesis_survives_bad_json():
    synth = WeeklySynthesizer(FakeClient("not json"))
    out = asyncio.run(synth.synthesize([_item("p")]))
    assert out == {
        "en": "(Synthesis failed; see the per-paper cards below.)",
        "zh": "(综合生成失败,见下方逐篇卡片。)",
    }
