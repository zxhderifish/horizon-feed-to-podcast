import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from src.scrapers.arxiv import ArxivScraper, parse_arxiv_atom
from src.models import SourceType

SAMPLE_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2507.01234v1</id>
    <updated>2026-07-03T00:00:00Z</updated>
    <published>2026-07-03T00:00:00Z</published>
    <title>Overlapping Communication in Pipeline-Parallel Training</title>
    <summary>We reduce all-reduce stalls on multi-GPU setups.</summary>
    <author><name>Jane Doe</name></author>
    <link href="http://arxiv.org/abs/2507.01234v1" rel="alternate" type="text/html"/>
  </entry>
</feed>
"""


def test_parse_arxiv_atom_maps_fields():
    items = parse_arxiv_atom(SAMPLE_ATOM)
    assert len(items) == 1
    item = items[0]
    assert item.source_type == SourceType.ARXIV
    assert item.id == "arxiv:paper:2507.01234"  # version stripped
    assert item.title == "Overlapping Communication in Pipeline-Parallel Training"
    assert "all-reduce" in item.content
    assert item.author == "Jane Doe"
    assert str(item.url).startswith("http://arxiv.org/abs/2507.01234")


def test_dedup_by_arxiv_id_prefers_first():
    items = parse_arxiv_atom(SAMPLE_ATOM)
    deduped = ArxivScraper._dedup(items + items)
    assert len(deduped) == 1


TWO_ENTRY_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2507.09999v1</id>
    <published>2026-07-05T00:00:00Z</published>
    <title>Recent Paper In Window</title>
    <summary>Fresh result.</summary>
    <author><name>New Author</name></author>
    <link href="http://arxiv.org/abs/2507.09999v1" rel="alternate" type="text/html"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <published>2024-01-02T00:00:00Z</published>
    <title>Old Paper Out Of Window</title>
    <summary>Stale result.</summary>
    <author><name>Old Author</name></author>
    <link href="http://arxiv.org/abs/2401.00001v1" rel="alternate" type="text/html"/>
  </entry>
</feed>
"""


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


class _RaisingClient:
    async def get(self, *args, **kwargs):
        raise RuntimeError("network down")


class _StaticClient:
    def __init__(self, text: str):
        self._text = text

    async def get(self, *args, **kwargs):
        return _FakeResponse(self._text)


def test_fetch_returns_empty_when_client_raises():
    scraper = ArxivScraper({}, _RaisingClient())
    since = datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert asyncio.run(scraper.fetch(since)) == []


def test_fetch_filters_out_of_window_papers():
    scraper = ArxivScraper({}, _StaticClient(TWO_ENTRY_ATOM))
    since = datetime(2026, 7, 1, tzinfo=timezone.utc)
    items = asyncio.run(scraper.fetch(since))
    assert len(items) == 1
    assert items[0].metadata["arxiv_id"] == "2507.09999"
    assert items[0].title == "Recent Paper In Window"


class _RecordingClient:
    """Captures the args/kwargs of the last get() call."""

    def __init__(self, text: str):
        self._text = text
        self.last_args = None
        self.last_kwargs = None

    async def get(self, *args, **kwargs):
        self.last_args = args
        self.last_kwargs = kwargs
        return _FakeResponse(self._text)


def test_fetch_uses_https_and_follows_redirects():
    # Regression: arXiv 301-redirects http->https and httpx does not follow
    # redirects by default, so the request must target https AND opt into
    # redirect-following, or fetch() silently returns nothing in production.
    client = _RecordingClient(TWO_ENTRY_ATOM)
    scraper = ArxivScraper({}, client)
    asyncio.run(scraper.fetch(datetime(2026, 7, 1, tzinfo=timezone.utc)))
    url = client.last_args[0] if client.last_args else client.last_kwargs.get("url")
    assert url.startswith("https://")
    assert client.last_kwargs.get("follow_redirects") is True
