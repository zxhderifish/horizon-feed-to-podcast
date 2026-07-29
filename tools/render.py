"""Render a daily issue.json into the Signal static site (deterministic)."""

import json
from pathlib import Path
from typing import List

from jinja2 import Environment, FileSystemLoader, select_autoescape

import config as app_config

ABBR = {"Hacker News": "HN", "Reddit": "RDT", "RSS": "RSS", "GitHub": "GH"}


def _env(templates_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def prepare_item(it: dict) -> dict:
    score = float(it["score"])
    out = dict(it)
    out["score_str"] = f"{score:.1f}"
    out["score_pct"] = f"{round(score * 10)}%"
    out["src_abbr"] = ABBR.get(it.get("source_type", ""), "•")
    return out


def render_feed(issue: dict, env: Environment, site: dict = None) -> str:
    items = [prepare_item(it) for it in issue["items"]]
    return env.get_template("feed.html.j2").render(
        issue=issue, items=items, site=site or app_config.get()["site"]
    )


def build_archive_entries(feed_dir: Path) -> List[dict]:
    entries = []
    for p in sorted(
        feed_dir.glob("20[0-9][0-9]-[0-1][0-9]-[0-3][0-9].html"), reverse=True
    ):
        meta_path = feed_dir / (p.stem + ".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        entries.append(
            {
                "date": p.stem,
                "count": meta.get("count", ""),
                "lead_en": meta.get("lead_en", ""),
                "lead_zh": meta.get("lead_zh", ""),
            }
        )
    return entries


def render_archive(entries: List[dict], env: Environment, site: dict = None) -> str:
    return env.get_template("archive.html.j2").render(
        entries=entries, site=site or app_config.get()["site"]
    )


def write_issue(
    issue: dict, templates_dir: Path, feed_dir: Path, site: dict = None
) -> List[str]:
    env = _env(templates_dir)
    site = site or app_config.get()["site"]
    html = render_feed(issue, env, site)
    written = []
    (feed_dir / "index.html").write_text(html, encoding="utf-8")
    written.append("index.html")
    dated = f"{issue['date']}.html"
    (feed_dir / dated).write_text(html, encoding="utf-8")
    written.append(dated)
    lead = issue["items"][0] if issue["items"] else {}
    lead_title = lead.get("title") or {}
    (feed_dir / f"{issue['date']}.meta.json").write_text(
        json.dumps(
            {
                "count": f"{issue.get('selected', '')}/{issue.get('total_fetched', '')}",
                "lead_en": lead_title.get("en", ""),
                "lead_zh": lead_title.get("zh", ""),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    entries = build_archive_entries(feed_dir)
    (feed_dir / "archive.html").write_text(
        render_archive(entries, env, site), encoding="utf-8"
    )
    written.append("archive.html")
    return written
