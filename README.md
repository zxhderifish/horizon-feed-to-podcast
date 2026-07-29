# horizon-feed-to-podcast

<div align="center">

**English** &nbsp;·&nbsp; [简体中文](README_zh.md)

</div>

---

A daily briefing that publishes itself twice: once as a web page, once as a
narrated podcast episode per language. A typical run reads about 40 items, keeps
15, writes them up, and has the episodes on Apple Podcasts before breakfast.

It exists because feed volume outgrows reading time, and because most AI
summarizers flatten everything into the same tone. Scoring is the part that
matters, and scoring is a judgment call, so it lives in a prompt that can be
edited rather than a ranking function that would have to be rewritten.

## What's actually in here

The important file is Markdown, not Python.

`skills/horizon-radar/SKILL.md` holds the editorial judgment: how to score an item
0–10, when two stories are the same story, how to write a segment someone will
listen to. A coding agent reads it every morning and does the work. When the
briefing degrades, that file is what changes.

`tools/` is the plumbing — dedupe, render, encode, upload, build RSS. Scripts,
because those steps should produce identical output every run.

```
fetch → dedupe → score → enrich → write → render → push site
                                              ↓
                          narrate → TTS → upload → RSS → push feeds
```

## Not a tech-news tool

The reference deployment covers AI and semiconductors; the machinery doesn't care.
The audience and topics are described in `config.toml`, and the scrapers point
wherever you aim them. The example config is deliberately a film/TV setup, so it's
clear the tech angle isn't baked in.

## Why not NotebookLM

It's the obvious first idea, and Audio Overview is genuinely good. But there's no
consumer API — only a per-seat Google Cloud product, which doesn't make sense for
a one-person daily show. The third-party wrappers are reverse-engineered and
break.

Generating the script locally turns out better anyway: the agent is already in the
loop, which means editorial control over structure and emphasis. The API only
handles the voice. Gemini TTS covers Chinese and English well and is free at this
volume.

## Requirements

- Python 3.11+, [uv](https://docs.astral.sh/uv/), `ffmpeg`
- A [Gemini API key](https://aistudio.google.com/apikey)
- Object storage for the mp3s. Cloudflare R2 works well here — podcast hosting
  cost is mostly egress, and R2 doesn't charge for it.
- Static hosting for the site and RSS files. Anything that serves a git repo.
- A coding agent that can run a prompt on a schedule.
  [Claude Code](https://claude.com/claude-code) scheduled tasks is what this was
  built against, and it's the one component without a drop-in substitute.

Using Cloudflare for storage and hosting?
[docs/cloudflare-setup.md](docs/cloudflare-setup.md) covers it click by click,
including two things that are easy to lose an afternoon to: R2's edge cache serves
deleted objects for a while, and `Content-Type` has to be set explicitly on
upload.

## Setup

```bash
git clone https://github.com/zxhderifish/horizon-feed-to-podcast
cd horizon-feed-to-podcast
cp config.example.toml config.toml
cp .env.example .env
```

Fill in `config.toml` first — domains, bucket, show names, voices. Most other
values derive from it. Both files are gitignored.

**1. Create a second repo for the output.** Code lives here; published files live
there. Make an empty repo, connect it to a static host, point a domain at it. The
pipeline writes HTML, RSS, `episodes.json`, and `seen.json` into it and pushes.

**2. Set up the bucket.** Create it, bind a custom domain (this is what makes
objects public — not the `r2.dev` URL), and issue a token scoped to that bucket
with object read and write. Credentials go in `.env`; bucket name and public URL
go in `config.toml`.

**3. Define what the briefing covers.** Two files:

- `[editorial]` in `config.toml` — the audience, in-scope topics, adjacent fields
  to admit, and what to reject regardless of prominence. Scoring reads this first.
- `horizon/data/config.example.json` — the feeds, subreddits, and repos to poll,
  plus the score threshold and quotas. Copy to `horizon/data/config.json`.

Keep them consistent. A rubric about architecture pointed at r/programming scores
everything as noise and publishes an empty page.

**4. Add cover art.** 1400×1400 minimum, at `covers/cover-<lang>.jpg` in the
output repo. `tools/make_covers.py` generates plain placeholders if the goal is
just to get the feed validating.

**5. Run it manually once.** Walk through `skills/horizon-radar/SKILL.md` and check
the page and first episode before automating. Then adapt
`examples/scheduled-task.md` for the scheduler.

**6. Submit the feed.** In Apple Podcasts Connect: **+ → New Show → Add a show
with an RSS feed**. One submission per language; they're separate shows. Most
other directories accept an RSS URL or sync from Apple's catalog.

## Supported sources

RSS, Hacker News, Reddit, GitHub releases, Telegram, Twitter/X, OpenBB,
OSSInsight. Only GitHub optionally wants a token (`GITHUB_TOKEN`, for rate
limits); Twitter needs a token or browser cookies.

If a source publishes a feed, it works — that covers most news sites, blogs,
journals, forums, YouTube channels, and mailing list archives. The other scrapers
exist because Hacker News and Reddit carry comments, which scoring uses as a
quality signal, and because GitHub releases are cleaner than their feeds.

No feed and no scraper? Use a feed bridge, or write one against
`horizon/src/scrapers/base.py`.

Worth knowing: `horizon/data/presets.json` ships eight ready-made source bundles
and all eight are technology, since upstream Horizon is a tech tool. Any other
subject means writing an RSS list by hand — a few lines per feed.

## The vendored scrapers are modified

`horizon/` is a vendored copy of
[Thysrael/Horizon](https://github.com/Thysrael/Horizon) (MIT), not the original.
`--fetch-only --json` was added so it fetches and dedupes without doing its own AI
summarizing, and the MCP layer and CI were removed as unused.
[`horizon/VENDORED.md`](horizon/VENDORED.md) lists every change. For stock
Horizon, go upstream.

## Notes on the narration

TTS drifts over long requests: ask for ten minutes in one call and the pacing
wanders and the last stretch degrades. So the script is split on `---` lines, each
segment synthesized separately, then concatenated.

Segment length matters more than expected. Below ~80 characters the delivery comes
out oddly emphatic; above ~800 quality falls off. 100–800 is the target range.
`***` marks the boundary before the rapid-fire block; `~~~` splits a long block
without inserting a sound between the halves.

Sound effects are optional and not included — see
[`assets/audio/README.md`](assets/audio/README.md) for the format and hook points.
A jingle that suits one subject will be wrong for another, so pick your own.

## Cost

Two ten-minute episodes a day is roughly 11 MB, about 4 GB a year. That fits R2's
free tier with room to spare, and R2 doesn't bill egress — the line item that
would otherwise dominate. Gemini TTS stays within its free tier at this volume;
budget a few cents a day past it. Two Pages deploys a day is well inside the free
build limit.

The real cost is the coding agent subscription, which is usually already being
paid for.

## Tests

```bash
cd tools && python -m pytest . -q
```

They run against `config.example.toml`, so a fresh clone passes before anything is
configured.

## License

MIT. The vendored `horizon/` is MIT as well — see `horizon/LICENSE` and
`horizon/VENDORED.md`.
