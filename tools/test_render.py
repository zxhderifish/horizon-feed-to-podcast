import json
from pathlib import Path

import config as app_config
import render


def test_prepare_item_computes_score_bar_and_abbr():
    it = {"score": 9.1, "source_type": "Hacker News", "title": {"en": "X", "zh": "叉"}}
    out = render.prepare_item(it)
    assert out["score_str"] == "9.1"
    assert out["score_pct"] == "91%"
    assert out["src_abbr"] == "HN"


def test_write_issue_emits_pages_and_archive(tmp_path):
    issue = json.loads(
        (Path(__file__).parent / "fixtures" / "issue.sample.json").read_text()
    )
    feed_dir = tmp_path / "feed"
    feed_dir.mkdir()
    site = app_config.load(app_config.EXAMPLE_PATH)["site"]
    written = render.write_issue(
        issue, Path(__file__).parent / "templates", feed_dir, site
    )
    assert "index.html" in written and "2026-06-15.html" in written and "archive.html" in written
    html = (feed_dir / "index.html").read_text()
    # bilingual content both present in DOM
    assert "TSMC starts 2nm" in html and "台积电启动 2 纳米试产" in html
    # score + computed bar width
    assert "9.1" in html and "91%" in html
    # optional field handling: item 2 has no background/discussion/refs
    assert html.count("EU enforces") >= 1
    # native details used for background (item 1 has it)
    assert "<details" in html
    # overview block: bilingual summary + bullets rendered before the feed
    assert "Semiconductors lead today" in html and "今天半导体是主轴" in html
    assert "EU opens its first enforcement window" in html
    assert 'class="overview"' in html
    archive = (feed_dir / "archive.html").read_text()
    assert "2026-06-15" in archive and "2/142" in archive
