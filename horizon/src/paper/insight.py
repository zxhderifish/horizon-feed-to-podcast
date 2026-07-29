"""原语② (part A): per-paper insight — objective four questions + taste block."""

import asyncio
from typing import List

from ..ai.utils import parse_json_response
from ..models import ContentItem

# A: paper-insight 客观四问; B: owner 品味判断
INSIGHT_KEYS = [
    "open_source", "task", "metrics", "improvement",     # A
    "core_idea", "mental_model", "reachable",            # B
    "confidence", "needs_web_check",                     # B
]

INSIGHT_SYSTEM = """你在为一位研究分布式训练系统的研究者做论文速读卡片。
基于标题和摘要,输出严格 JSON,含且仅含这些键。每个文本字段都必须是
{"en": <English>, "zh": <中文>} 的对象(双语),needs_web_check 除外(布尔)。
A. 客观四问:
  "open_source": 有没有开源代码?(有/无 + 链接,不确定写"未提及")
  "task": 它解决什么任务?
  "metrics": 评测指标 + 数据集?
  "improvement": 相对 baseline 的核心改进?
B. 品味判断:
  "core_idea": 一句话核心 idea。
  "mental_model": 它印证/挑战了分布式训练里的哪条直觉?
  "reachable": 本地(消费级/单机/小集群)够得着吗?值不值得亲手复现?
  "confidence": 置信度,如 {"en": "medium", "zh": "中"}。
  "needs_web_check": true/false(截止训练日之后的具体数字/型号是否需要联网核实)。
示例形状:{"task": {"en": "...", "zh": "..."}, ..., "needs_web_check": false}
只输出 JSON,不要额外文字。"""


class PaperInsighter:
    def __init__(self, ai_client):
        self.client = ai_client

    async def _annotate_one(self, item: ContentItem) -> None:
        user = f"标题: {item.title}\n\n摘要: {(item.content or '')[:2000]}"
        insight = {
            k: (False if k == "needs_web_check" else {"en": "", "zh": ""})
            for k in INSIGHT_KEYS
        }
        try:
            raw = await self.client.complete(INSIGHT_SYSTEM, user)
            parsed = parse_json_response(raw) or {}
            for k in INSIGHT_KEYS:
                if k in parsed:
                    insight[k] = parsed[k]
        except Exception:
            pass
        item.metadata["insight"] = insight

    async def annotate(self, items: List[ContentItem]) -> None:
        await asyncio.gather(*(self._annotate_one(it) for it in items))
