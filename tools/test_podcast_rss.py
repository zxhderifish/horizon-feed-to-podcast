import json
import xml.etree.ElementTree as ET
from pathlib import Path

import config as app_config
import podcast_rss

ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
ATOM = "{http://www.w3.org/2005/Atom}"


def _cfg():
    """Tests run against the shipped example config, so a fresh clone is green."""
    return app_config.load(app_config.EXAMPLE_PATH)


def _episodes():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "episodes.sample.json").read_text()
    )


def test_build_rss_channel_metadata():
    cfg = _cfg()
    show = cfg["podcast"]["shows"]["zh"]
    ch = ET.fromstring(podcast_rss.build_rss(_episodes(), "zh", cfg)).find("channel")
    assert ch.find("title").text == show["title"]
    assert ch.find("language").text == show["language"]
    assert ch.find(f"{ITUNES}author").text == show["author"]
    assert ch.find(f"{ITUNES}explicit").text == "false"
    assert ch.find(f"{ITUNES}image").get("href").endswith("cover-zh.jpg")
    assert ch.find(f"{ITUNES}category").get("text") == cfg["podcast"]["category"]
    owner_email = ch.find(f"{ITUNES}owner/{ITUNES}email")
    assert owner_email.text == cfg["podcast"]["owner_email"]
    self_link = ch.find(f"{ATOM}link")
    assert self_link.get("rel") == "self"
    assert self_link.get("href") == show["feed_url"]
    assert self_link.get("type") == "application/rss+xml"


def test_urls_are_derived_from_hosting_block():
    cfg = _cfg()
    site = cfg["hosting"]["site_url"]
    for lang, show in cfg["podcast"]["shows"].items():
        assert show["feed_url"] == f"{site}/podcast-{lang}.xml"
        assert show["cover"] == f"{site}/covers/cover-{lang}.jpg"


def test_build_rss_filters_lang_and_sorts_newest_first():
    ch = ET.fromstring(podcast_rss.build_rss(_episodes(), "zh", _cfg())).find("channel")
    items = ch.findall("item")
    assert len(items) == 2  # en episode excluded
    assert "2026-07-28" in items[0].find("title").text
    assert "2026-07-27" in items[1].find("title").text


def test_build_rss_item_fields():
    xml = podcast_rss.build_rss(_episodes(), "en", _cfg())
    item = ET.fromstring(xml).find("channel").findall("item")[0]
    enc = item.find("enclosure")
    assert enc.get("url") == "https://podcast.example.com/2026-07-28-en.mp3"
    assert enc.get("length") == "7123456"
    assert enc.get("type") == "audio/mpeg"
    guid = item.find("guid")
    assert guid.text == enc.get("url") and guid.get("isPermaLink") == "false"
    assert item.find(f"{ITUNES}duration").text == "540"
    pub = item.find("pubDate").text
    assert "Jul 2026" in pub  # RFC 2822
    assert "12:30:00 +0000" in pub  # fixed 12:30 UTC pub time


def test_build_rss_dedupes_same_date_lang_keeping_last():
    eps = _episodes()
    updated = dict(eps[0])  # same date+lang as eps[0], new mp3
    updated["mp3_url"] = "https://podcast.example.com/2026-07-28-zh-v2.mp3"
    xml = podcast_rss.build_rss(eps + [updated], "zh", _cfg())
    items = ET.fromstring(xml).find("channel").findall("item")
    assert len(items) == 2  # not 3: duplicate (date, lang) collapsed
    assert items[0].find("guid").text.endswith("-zh-v2.mp3")  # last wins


def test_html_description_paragraphs_and_linked_list():
    text = (
        "Today's throughline is AI capex.\n\n"
        "In this episode:\n"
        "1. A position on open weights https://example.com/open-weights\n"
        "2. A 2.8T model ships its weights https://example.com/kimi-k3"
    )
    html = podcast_rss.html_description(text)
    assert "<p>Today&#x27;s throughline is AI capex.</p>" in html
    assert "<p>In this episode:</p>" in html
    assert html.count("<li>") == 2 and "<ol>" in html
    assert '<a href="https://example.com/open-weights">' in html
    # the URL is not duplicated as bare text next to its link
    assert "</a> https://" not in html
    # list numbers come from <ol>, not literal text
    assert "<li>1." not in html


def test_channel_description_is_html_with_plaintext_summary():
    ch = ET.fromstring(podcast_rss.build_rss(_episodes(), "zh", _cfg())).find("channel")
    assert ch.find("description").text.startswith("<p>")
    assert "<br />" in ch.find("description").text  # source list keeps its lines
    assert "\n" in ch.find(f"{ITUNES}summary").text  # plain-text fallback intact


def test_html_description_escapes_markup():
    html = podcast_rss.html_description("a < b & c")
    assert "&lt;" in html and "&amp;" in html and "<p>" in html


def test_build_rss_item_description_is_html():
    eps = _episodes()
    eps[0]["description"] = "Lead.\n\nItems:\n1. Title https://example.com/a"
    xml = podcast_rss.build_rss(eps, "zh", _cfg())
    desc = ET.fromstring(xml).find("channel").find("item").find("description").text
    assert desc.startswith("<p>") and '<a href="https://example.com/a">' in desc


def test_write_feeds_emits_one_file_per_configured_show(tmp_path):
    cfg = _cfg()
    (tmp_path / "episodes.json").write_text(json.dumps(_episodes()), encoding="utf-8")
    written = podcast_rss.write_feeds(tmp_path, cfg)
    assert sorted(written) == ["podcast-en.xml", "podcast-zh.xml"]
    for lang, show in cfg["podcast"]["shows"].items():
        xml = (tmp_path / f"podcast-{lang}.xml").read_text(encoding="utf-8")
        assert xml.startswith("<?xml") and show["title"] in xml
