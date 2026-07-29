"""arXiv Atom API scraper (paper radar). HF Daily Papers deferred — see spec Open Questions."""

import logging
import re
from datetime import datetime
from typing import List

import feedparser
import httpx

from .base import BaseScraper
from ..models import ContentItem, SourceType

logger = logging.getLogger(__name__)

ARXIV_API = "https://export.arxiv.org/api/query"
_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})")


def _bare_arxiv_id(raw: str) -> str:
    """Strip URL prefix and version suffix: 'http://arxiv.org/abs/2507.01234v2' -> '2507.01234'."""
    m = _ARXIV_ID_RE.search(raw or "")
    return m.group(1) if m else (raw or "").strip()


def parse_arxiv_atom(atom_text: str) -> List[ContentItem]:
    """Parse an arXiv Atom API response into ContentItems."""
    feed = feedparser.parse(atom_text)
    items: List[ContentItem] = []
    for entry in feed.entries:
        arxiv_id = _bare_arxiv_id(entry.get("id", ""))
        if not arxiv_id:
            continue
        published = entry.get("published", "")
        try:
            published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            # Can't determine publication date -> skip rather than emit an item
            # that would falsely pass the published_at >= since window filter.
            continue
        author = None
        if entry.get("authors"):
            author = entry.authors[0].get("name")
        items.append(
            ContentItem(
                id=f"arxiv:paper:{arxiv_id}",
                source_type=SourceType.ARXIV,
                title=(entry.get("title") or "").strip().replace("\n", " "),
                url=entry.get("link") or f"http://arxiv.org/abs/{arxiv_id}",
                content=(entry.get("summary") or "").strip(),
                author=author,
                published_at=published_at,
                metadata={"arxiv_id": arxiv_id},
            )
        )
    return items


class ArxivScraper(BaseScraper):
    """Fetch recent papers from arXiv categories + keyword query."""

    def __init__(self, config: dict, http_client: httpx.AsyncClient):
        super().__init__(config, http_client)
        self.categories: List[str] = config.get("categories", ["cs.DC", "cs.LG"])
        self.keywords: List[str] = config.get("keywords", [])
        self.max_results: int = config.get("max_results", 100)

    def _build_query(self) -> str:
        cat_clause = " OR ".join(f"cat:{c}" for c in self.categories)
        query = f"({cat_clause})"
        if self.keywords:
            kw_clause = " OR ".join(f'all:"{k}"' for k in self.keywords)
            query = f"{query} AND ({kw_clause})"
        return query

    @staticmethod
    def _dedup(items: List[ContentItem]) -> List[ContentItem]:
        seen = set()
        out = []
        for item in items:
            key = item.metadata.get("arxiv_id", item.id)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    async def fetch(self, since: datetime) -> List[ContentItem]:
        params = {
            "search_query": self._build_query(),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": str(self.max_results),
        }
        try:
            resp = await self.client.get(
                ARXIV_API, params=params, timeout=30.0, follow_redirects=True
            )
            resp.raise_for_status()
            items = parse_arxiv_atom(resp.text)
        except Exception as e:
            logger.warning("arXiv fetch failed: %s", e)
            items = []
        items = [it for it in items if it.published_at >= since]
        return self._dedup(items)
