"""Render the weekly paper report: structured issue + zh-side local markdown."""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from ..models import ContentItem

# insight key -> (en label, zh label) for the local markdown
_CARD_LABELS = [
    ("core_idea", "核心 idea"),
    ("task", "任务"),
    ("metrics", "指标/数据集"),
    ("improvement", "对比 baseline"),
    ("open_source", "开源"),
    ("mental_model", "心智模型"),
    ("reachable", "够得着 / 值不值得复现"),
    ("confidence", "置信度"),
]

_INSIGHT_KEYS = [
    "open_source", "task", "metrics", "improvement",
    "core_idea", "mental_model", "reachable", "confidence", "needs_web_check",
]


def iso_week(dt: datetime) -> str:
    """ISO year-week like '2026-W27' (matches Liquid/JS `%G-W%V`)."""
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def _bilingual(value) -> Dict[str, str]:
    """Coerce an insight field to a {en,zh} dict (defensive against old shapes)."""
    if isinstance(value, dict):
        return {"en": str(value.get("en", "")), "zh": str(value.get("zh", ""))}
    return {"en": str(value), "zh": str(value)}


def build_paper_issue(week: str, generated: str, total_fetched: int,
                      synthesis: Dict[str, str], items: List[ContentItem]) -> dict:
    """Assemble the structured, bilingual paper issue dict (papers sorted by score desc)."""
    ordered = sorted(items, key=lambda it: it.ai_score or 0, reverse=True)
    papers = []
    for it in ordered:
        raw = it.metadata.get("insight", {})
        insight = {}
        for k in _INSIGHT_KEYS:
            if k == "needs_web_check":
                insight[k] = bool(raw.get(k, False))
            else:
                insight[k] = _bilingual(raw.get(k, {}))
        papers.append({
            "title": it.title,
            "url": str(it.url),
            "arxiv_id": it.metadata.get("arxiv_id", ""),
            "score": float(it.ai_score or 0),
            "insight": insight,
        })
    return {
        "week": week,
        "generated": generated,
        "total_fetched": total_fetched,
        "selected": len(papers),
        "synthesis": {"en": str(synthesis.get("en", "")),
                      "zh": str(synthesis.get("zh", ""))},
        "papers": papers,
    }


def _zh(value) -> str:
    return value.get("zh", "") if isinstance(value, dict) else str(value)


def _render_card(item: ContentItem) -> str:
    ins = item.metadata.get("insight", {})
    head = f"### [{item.title}]({item.url}) — score {item.ai_score}\n"
    rows = [f"- **{label}**: {_zh(ins.get(key, ''))}" for key, label in _CARD_LABELS]
    if ins.get("needs_web_check"):
        rows.append("- ⚠️ **需联网核实**")
    return head + "\n".join(rows) + "\n"


def build_markdown(week: str, synthesis: Dict[str, str], items: List[ContentItem]) -> str:
    """Local zh-side markdown for offline reading."""
    ordered = sorted(items, key=lambda it: it.ai_score or 0, reverse=True)
    parts = [f"# 本周论文雷达 · {week}\n", "## 本周综合\n", _zh(synthesis) + "\n",
             "## 逐篇 insight\n"]
    parts += [_render_card(it) for it in ordered]
    return "\n".join(parts)


def write_local(markdown: str, issue: dict, summaries_dir: Path) -> Dict[str, Path]:
    """Write the zh-side .md and the structured paper-issue json (both gitignored, local)."""
    summaries_dir = Path(summaries_dir)
    summaries_dir.mkdir(parents=True, exist_ok=True)
    week = issue["week"]
    summary_path = summaries_dir / f"paper-{week}.md"
    summary_path.write_text(markdown, encoding="utf-8")
    issue_path = summaries_dir / f"paper-issue-{week}.json"
    issue_path.write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"summary": summary_path, "issue_json": issue_path}
