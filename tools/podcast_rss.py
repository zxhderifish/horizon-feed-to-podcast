"""Build Apple/小宇宙-compliant podcast RSS from episodes.json (deterministic)."""

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import escape
from email.utils import format_datetime
from pathlib import Path
from typing import List

import config as app_config

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ATOM_NS = "http://www.w3.org/2005/Atom"

# Feed pubDate is fixed at 12:30 UTC, shortly after the daily run.
PUB_HOUR_UTC = 12
PUB_MINUTE_UTC = 30


_URL_RE = re.compile(r"https?://\S+")
_NUMBERED_RE = re.compile(r"^\s*\d+[.、]\s*(.+)$")


def _linkify(text: str) -> str:
    """Escape text, turning bare URLs into anchors."""
    out, pos = [], 0
    for m in _URL_RE.finditer(text):
        out.append(escape(text[pos : m.start()]))
        url = m.group(0).rstrip(".,;:)")
        out.append(f'<a href="{escape(url, quote=True)}">{escape(url)}</a>')
        pos = m.start() + len(url)
    out.append(escape(text[pos:]))
    return "".join(out)


def _list_item(body: str) -> str:
    """A numbered entry: link the title text, absorbing its trailing URL."""
    m = _URL_RE.search(body)
    if not m:
        return f"<li>{escape(body.strip())}</li>"
    url = m.group(0).rstrip(".,;:)")
    label = (body[: m.start()] + body[m.start() + len(url) :]).strip() or url
    return f'<li><a href="{escape(url, quote=True)}">{escape(label)}</a></li>'


def html_description(text: str) -> str:
    """Render plain-text show notes as HTML — podcast clients render, not preserve,
    the description, so newlines must become real markup and URLs real links."""
    html = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        buf, items = [], []

        def flush_text():
            if buf:
                html.append("<p>" + "<br />".join(_linkify(x) for x in buf) + "</p>")
                buf.clear()

        def flush_items():
            if items:
                html.append("<ol>" + "".join(items) + "</ol>")
                items.clear()

        for ln in lines:
            m = _NUMBERED_RE.match(ln)
            if m:
                flush_text()
                items.append(_list_item(m.group(1)))
            else:
                flush_items()
                buf.append(ln.strip())
        flush_text()
        flush_items()
    return "".join(html)


def _pub_date(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=PUB_HOUR_UTC, minute=PUB_MINUTE_UTC, tzinfo=timezone.utc
    )
    return format_datetime(dt)


def build_rss(episodes: List[dict], lang: str, cfg: dict = None) -> str:
    cfg = cfg or app_config.get()
    show = cfg["podcast"]["shows"][lang]
    podcast = cfg["podcast"]
    ET.register_namespace("itunes", ITUNES_NS)
    ET.register_namespace("atom", ATOM_NS)
    rss = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = show["title"]
    ET.SubElement(ch, "link").text = show["link"]
    ET.SubElement(ch, "description").text = html_description(show["description"])
    ET.SubElement(ch, f"{{{ITUNES_NS}}}summary").text = show["description"]
    ET.SubElement(ch, "language").text = show["language"]
    ET.SubElement(ch, f"{{{ITUNES_NS}}}author").text = show["author"]
    ET.SubElement(ch, f"{{{ITUNES_NS}}}explicit").text = "false"
    ET.SubElement(ch, f"{{{ITUNES_NS}}}image", {"href": show["cover"]})
    ET.SubElement(ch, f"{{{ITUNES_NS}}}category", {"text": podcast["category"]})
    owner = ET.SubElement(ch, f"{{{ITUNES_NS}}}owner")
    ET.SubElement(owner, f"{{{ITUNES_NS}}}email").text = podcast["owner_email"]
    ET.SubElement(
        ch,
        f"{{{ATOM_NS}}}link",
        {"href": show["feed_url"], "rel": "self", "type": "application/rss+xml"},
    )

    # Dedupe by (date, lang), last entry wins — a re-run appending a corrected
    # episode must not produce duplicate guids in the feed.
    deduped = {(e["date"], e["lang"]): e for e in episodes}.values()
    eps = sorted(
        (e for e in deduped if e["lang"] == lang),
        key=lambda e: e["date"],
        reverse=True,
    )
    for ep in eps:
        item = ET.SubElement(ch, "item")
        ET.SubElement(item, "title").text = ep["title"]
        ET.SubElement(item, "description").text = html_description(ep["description"])
        ET.SubElement(item, f"{{{ITUNES_NS}}}summary").text = ep["description"]
        ET.SubElement(
            item,
            "enclosure",
            {"url": ep["mp3_url"], "length": str(ep["bytes"]), "type": "audio/mpeg"},
        )
        guid = ET.SubElement(item, "guid", {"isPermaLink": "false"})
        guid.text = ep["mp3_url"]
        ET.SubElement(item, "pubDate").text = _pub_date(ep["date"])
        ET.SubElement(item, f"{{{ITUNES_NS}}}duration").text = str(
            int(ep["duration_s"])
        )

    body = ET.tostring(rss, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body


def write_feeds(feed_dir: Path, cfg: dict = None) -> List[str]:
    cfg = cfg or app_config.get()
    episodes = json.loads(
        (feed_dir / "episodes.json").read_text(encoding="utf-8")
    )
    written = []
    for lang in cfg["podcast"]["shows"]:
        name = f"podcast-{lang}.xml"
        (feed_dir / name).write_text(build_rss(episodes, lang, cfg), encoding="utf-8")
        written.append(name)
    return written


if __name__ == "__main__":
    import sys

    print(write_feeds(Path(sys.argv[1])))
