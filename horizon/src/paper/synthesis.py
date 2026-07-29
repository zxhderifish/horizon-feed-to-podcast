"""原语② (part B): weekly cross-paper synthesis (bilingual)."""

from typing import Dict, List

from ..ai.utils import parse_json_response
from ..models import ContentItem

SYNTHESIS_SYSTEM = """你在为一位研究分布式训练系统的研究者写「本周论文综合」。
基于这一批论文的标题+核心 idea,写一段综合(不要逐篇复述),覆盖:
1. 这周有没有收敛到某个主题?几篇在讲同一件事?
2. 有没有互相矛盾 / 和已知常识冲突?
3. 若只挑一篇亲手复现,是哪篇、为什么。
输出严格 JSON:{"en": <English 段落>, "zh": <中文段落>},各控制在 200 字/words 内,
直给结论,不要客套。只输出 JSON。"""

_EMPTY = {"en": "No qualifying papers this week.", "zh": "本周无达标论文。"}
_FAILED = {
    "en": "(Synthesis failed; see the per-paper cards below.)",
    "zh": "(综合生成失败,见下方逐篇卡片。)",
}


def _idea_zh(item: ContentItem) -> str:
    idea = item.metadata.get("insight", {}).get("core_idea", "")
    return idea.get("zh", "") if isinstance(idea, dict) else str(idea)


class WeeklySynthesizer:
    def __init__(self, ai_client):
        self.client = ai_client

    async def synthesize(self, items: List[ContentItem]) -> Dict[str, str]:
        if not items:
            return dict(_EMPTY)
        lines = [f"- {it.title}(score {it.ai_score}): {_idea_zh(it)}" for it in items]
        user = "本周达标论文:\n" + "\n".join(lines)
        try:
            raw = await self.client.complete(SYNTHESIS_SYSTEM, user)
            parsed = parse_json_response(raw)
            en = str(parsed.get("en", "")) if parsed else ""
            zh = str(parsed.get("zh", "")) if parsed else ""
            if not en and not zh:
                return dict(_FAILED)
            return {"en": en, "zh": zh}
        except Exception:
            return dict(_FAILED)
