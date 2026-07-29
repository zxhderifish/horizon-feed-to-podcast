import json
from datetime import datetime, timezone

from src.paper.render import build_paper_issue, build_markdown, iso_week, write_local
from src.models import ContentItem, SourceType


def _item(title, score):
    it = ContentItem(
        id=f"arxiv:paper:{title}", source_type=SourceType.ARXIV, title=title,
        url="http://arxiv.org/abs/2507.01234", content="abstract",
        published_at=datetime(2026, 7, 3, tzinfo=timezone.utc), ai_score=score,
    )
    it.metadata["arxiv_id"] = "2507.01234"
    it.metadata["insight"] = {
        "open_source": {"en": "yes", "zh": "有"},
        "task": {"en": "task-en", "zh": "任务-zh"},
        "metrics": {"en": "m-en", "zh": "m-zh"},
        "improvement": {"en": "i-en", "zh": "i-zh"},
        "core_idea": {"en": "idea-en", "zh": "idea-zh"},
        "mental_model": {"en": "mm-en", "zh": "mm-zh"},
        "reachable": {"en": "r-en", "zh": "r-zh"},
        "confidence": {"en": "medium", "zh": "中"},
        "needs_web_check": False,
    }
    return it


SYNTH = {"en": "SYNTH-EN", "zh": "SYNTH-ZH"}


def test_iso_week_format():
    assert iso_week(datetime(2026, 7, 3, tzinfo=timezone.utc)) == "2026-W27"


def test_build_paper_issue_shape_and_sorting():
    items = [_item("Low", 7.0), _item("High", 9.0)]
    issue = build_paper_issue(
        week="2026-W27", generated="2026-07-07",
        total_fetched=42, synthesis=SYNTH, items=items,
    )
    assert issue["week"] == "2026-W27"
    assert issue["generated"] == "2026-07-07"
    assert issue["total_fetched"] == 42
    assert issue["selected"] == 2
    assert issue["synthesis"] == SYNTH
    assert [p["title"] for p in issue["papers"]] == ["High", "Low"]
    p = issue["papers"][0]
    assert p["score"] == 9.0
    assert p["arxiv_id"] == "2507.01234"
    assert p["insight"]["core_idea"] == {"en": "idea-en", "zh": "idea-zh"}


def test_build_markdown_uses_zh_side():
    md = build_markdown("2026-W27", SYNTH, [_item("Paper A", 9.0)])
    assert "SYNTH-ZH" in md and "SYNTH-EN" not in md
    assert "Paper A" in md
    assert "idea-zh" in md
    assert "core_idea" not in md
    assert "{'en'" not in md


def test_needs_web_check_renders_badge_and_bool():
    it = _item("Paper W", 8.0)
    it.metadata["insight"]["needs_web_check"] = True
    issue = build_paper_issue(
        week="2026-W27", generated="2026-07-07",
        total_fetched=1, synthesis=SYNTH, items=[it],
    )
    assert issue["papers"][0]["insight"]["needs_web_check"] is True
    md = build_markdown("2026-W27", SYNTH, [it])
    assert "需联网核实" in md


def test_legacy_string_insight_is_coerced():
    it = _item("Legacy", 8.0)
    it.metadata["insight"] = {
        "open_source": "有", "task": "任务", "metrics": "指标",
        "improvement": "改进", "core_idea": "点子", "mental_model": "心智",
        "reachable": "双卡", "confidence": "中", "needs_web_check": False,
    }
    issue = build_paper_issue(
        week="2026-W27", generated="2026-07-07",
        total_fetched=1, synthesis=SYNTH, items=[it],
    )
    assert issue["papers"][0]["insight"]["core_idea"] == {"en": "点子", "zh": "点子"}
    md = build_markdown("2026-W27", SYNTH, [it])
    assert "点子" in md


def test_write_local_creates_md_and_json(tmp_path):
    summaries = tmp_path / "summaries"
    issue = build_paper_issue(
        week="2026-W27", generated="2026-07-07",
        total_fetched=42, synthesis=SYNTH, items=[_item("Paper A", 9.0)],
    )
    md = build_markdown("2026-W27", SYNTH, [_item("Paper A", 9.0)])
    paths = write_local(md, issue, summaries_dir=summaries)
    assert paths["summary"].name == "paper-2026-W27.md"
    assert paths["summary"].read_text(encoding="utf-8") == md
    loaded = json.loads(paths["issue_json"].read_text(encoding="utf-8"))
    assert loaded["week"] == "2026-W27" and loaded["papers"][0]["title"] == "Paper A"
