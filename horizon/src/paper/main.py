"""CLI entry point for the weekly paper radar (decoupled from news `main`)."""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from rich.console import Console

from .config import load_paper_config
from .insight import PaperInsighter
from .render import build_markdown, build_paper_issue, iso_week, write_local
from .scorer import TasteScorer
from .synthesis import WeeklySynthesizer
from ..ai.client import create_ai_client
from ..scrapers.arxiv import ArxivScraper

console = Console()


async def _run(config_path: Path, feed_dir: str | None) -> None:
    cfg = load_paper_config(config_path)
    ai_client = create_ai_client(cfg.ai)
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=cfg.time_window_days)

    async with httpx.AsyncClient() as http:
        scraper = ArxivScraper(cfg.arxiv.model_dump(), http)
        items = await scraper.fetch(since)
    console.print(f"📥 arXiv fetched {len(items)} papers")

    kept = await TasteScorer(ai_client, cfg.score_threshold).score_and_filter(items)
    console.print(f"⭐️ {len(kept)} papers scored ≥ {cfg.score_threshold}")

    await PaperInsighter(ai_client).annotate(kept)
    synthesis = await WeeklySynthesizer(ai_client).synthesize(kept)

    week = iso_week(now)
    generated = now.strftime("%Y-%m-%d")
    issue = build_paper_issue(
        week=week, generated=generated, total_fetched=len(items),
        synthesis=synthesis, items=kept,
    )
    markdown = build_markdown(week, synthesis, kept)
    paths = write_local(markdown, issue, summaries_dir=Path("data/summaries"))
    console.print(f"💾 Wrote {paths['summary']} and {paths['issue_json']}")

    if feed_dir:
        # render_paper lives in ../tools (parallel to the news renderer)
        tools_dir = Path(__file__).resolve().parents[3] / "tools"
        sys.path.insert(0, str(tools_dir))
        import render_paper
        written = render_paper.write_paper_issue(
            issue, tools_dir / "templates", Path(feed_dir))
        console.print(f"🌐 Published to feed: {', '.join(written)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Horizon Paper Radar — weekly")
    parser.add_argument("--config", default="data/config.paper.json",
                        help="Path to paper config (default: data/config.paper.json)")
    parser.add_argument("--feed-dir", default=None,
                        help="horizon-feed repo path to publish into (skip if omitted)")
    args = parser.parse_args()
    load_dotenv()
    asyncio.run(_run(Path(args.config), args.feed_dir))


if __name__ == "__main__":
    main()
