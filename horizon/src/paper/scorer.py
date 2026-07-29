"""原语①: Taste Scorer — encodes the owner's taste as a scoring rubric.

Reuses the shared AIClient; independent of the news analyzer.
"""

import asyncio
from typing import List, Optional

from ..ai.utils import parse_json_response
from ..models import ContentItem

TASTE_RUBRIC_SYSTEM = """你是一个论文评审助手,替一位研究者按他的品味给论文打 0–10 分。
研究者的方向:分布式 / 多卡并行 / 分布式训练系统,落在本地小模型场景。评分维度:
1. 主题命中:是不是分布式/多卡并行/分布式训练系统 + 本地小模型场景。命中 → 高分。
2. 理解价值 > 工程堆料:偏系统原理、能建全局图景的加分;纯刷 leaderboard 减分。
3. 本地可部署尺度:广义「本地可部署」(消费级/单机/小集群)都算,不看具体某张卡的性能;
   只把千卡大厂专属、无法映射到小规模的排除。
4. 反过度设计:只为大厂千卡场景独占服务的减分;能映射到小规模的加分。
只输出 JSON:{"score": <0-10 number>, "reason": "<一句中文理由>"}"""


class TasteScorer:
    def __init__(self, ai_client, threshold: float = 7.0):
        self.client = ai_client
        self.threshold = threshold

    async def _score_one(self, item: ContentItem) -> None:
        user = f"标题: {item.title}\n\n摘要: {(item.content or '')[:1500]}"
        try:
            raw = await self.client.complete(TASTE_RUBRIC_SYSTEM, user)
            parsed = parse_json_response(raw) or {}
            item.ai_score = float(parsed.get("score", 0))
            item.ai_reason = str(parsed.get("reason", ""))
        except Exception:
            item.ai_score = 0.0
            item.ai_reason = "scoring failed"

    async def score_and_filter(self, items: List[ContentItem]) -> List[ContentItem]:
        await asyncio.gather(*(self._score_one(it) for it in items))
        kept = [it for it in items if (it.ai_score or 0) >= self.threshold]
        kept.sort(key=lambda it: it.ai_score or 0, reverse=True)
        return kept
