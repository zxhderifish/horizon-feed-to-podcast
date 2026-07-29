# Vendored Horizon

This `horizon/` directory is a **vendored copy** of
[Thysrael/Horizon](https://github.com/Thysrael/Horizon) (MIT License — see `LICENSE`),
included so that this project is self-contained and runnable from a single clone.

It is no longer a standalone git repository (its original `.git` was removed). All
files are tracked directly by the parent repo.

## Why it's here

This project keeps Horizon's deterministic Python scrapers and reuses them via the
`horizon --fetch-only --json` entry point. The fetch path (`orchestrator.py`, `scrapers/`,
`models.py`, `storage/`, etc.) is tightly coupled, so the whole `src/` tree is vendored
rather than a hand-picked subset.

## Local modifications vs upstream

1. **Added** `src/fetch_only.py` + `--fetch-only --json` flags in `src/main.py` —
   a deterministic fetch + cross-source URL dedup path that emits JSON, with no AI calls
   and no API key. (This change alone is upstreamable to Horizon.)
2. **Removed** the entire MCP layer (`src/mcp/`, `scripts/check_mcp.py`, MCP tests,
   the `horizon-mcp` script entry, and the `mcp` dependency) — local-only, NOT for upstream.
3. **Removed** Horizon's `.github/` CI workflows (they belong to the upstream project).

## Updating from upstream

To re-sync with upstream Horizon later, re-clone it, re-apply the two changes above
(fetch-only entry point; MCP removal), and replace this directory.
