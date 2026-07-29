---
name: horizon-radar
description: Use for the daily Horizon news-radar run — fetch via horizon --fetch-only, dedup against seen.json, then score, filter, enrich, and write a bilingual (EN+ZH) briefing.
---

# Horizon Radar — daily run

Paths below use two placeholders: `<repo>` is this repository's clone, and
`<site>` is the separate publishing repo whose contents are served as your
static site (see README).

Produce a daily bilingual news briefing from configured sources.

## Procedure

1. **Fetch (deterministic).** Run:
   `cd <repo>/horizon && uv run horizon --fetch-only --json` (add `--hours N` to override the window). Parse the JSON array of items from stdout. Ignore stderr progress.

2. **Drop already-seen.** Load `<repo>/seen.json` (an object mapping url -> ISO timestamp; treat a missing file as `{}`). Use `tools/dedup.py`'s `filter_unseen(items, ledger)` to keep only new items (it returns `(fresh_items, new_entries)`).

3. **Score 0–10.** Read the `[editorial]` block in `config.toml` first — `audience`,
   `topics`, `also_in_scope`, and `out_of_scope` define what this briefing is *about*.
   Judge each item's importance to that reader. The rubric bands and signals below are
   domain-agnostic; the subject matter is entirely yours to configure.

   - **9–10 (Groundbreaking):** Major breakthroughs or paradigm shifts in the configured
     field — landmark releases, significant research results, industry-changing
     announcements. These demand immediate attention from anyone in that field.
   - **7–8 (High Value):** Important developments worth reading soon — substantive
     deep-dives, novel approaches to known problems, insightful analysis or commentary,
     genuinely useful new work.
   - **5–6 (Interesting):** Worth knowing but not urgent — incremental improvements, useful tutorials, content with moderate community interest.
   - **3–4 (Low Priority):** Generic or routine — minor updates, common knowledge restated, or overly promotional content.
   - **0–2 (Noise):** Not relevant or low quality — spam, purely promotional material, off-topic content, or trivial updates.

   Scoring signals to weigh:
   - **Depth and novelty:** Is this genuinely new, or a rehash?
   - **Potential impact on the field:** Who is affected, and how broadly?
   - **Quality of writing/presentation:** Is it well-crafted or thin?
   - **Relevance to `audience` and `topics`** as configured — this is the primary signal.
   - **Community discussion quality:** Insightful comments, diverse viewpoints, and substantive debates increase value. High upvotes/favorites with substantive discussion indicate community-validated importance.
   - **Adjacent fields, per `also_in_scope`:** where config admits a neighbouring domain
     (say, market news for a technology audience), score those items on their impact to
     the configured reader specifically — not on their importance within that other
     field. Everything `out_of_scope` names is noise regardless of how prominent it is.

   Record a one-line reason per item explaining the score (mention discussion quality if comments were provided).

4. **Filter + sort.** Keep items with score ≥ the threshold in `horizon/data/config.json` (`filtering.ai_score_threshold`); sort descending by score.

5. **Topic dedup.** Identify groups of items that cover the exact same real-world event, release, or announcement (not just the same product). Within each duplicate group, keep the highest-scored item and merge the content/comments of dropped duplicates into it. Items about the same product but different events are NOT duplicates (e.g., "Gemma 4 released" vs "Gemma 4 jailbroken"). Err on the side of keeping items separate when unsure.

6. **Quota.** Apply per-category and global caps mirroring `apply_balanced_digest`: sort remaining items by score descending; for each item determine its category group from `filtering.category_groups` in config (first matching group wins if a category appears in multiple groups); skip an item if its group has already hit its configured `limit`; after all group limits are applied, truncate to the global `filtering.max_items` cap if set.

   Additionally, cap **market-category items** (those whose source `metadata.category == "market"`, i.e. from the market RSS feeds) at **5 per issue** — keep the highest-scored market items and drop the rest. Market items are otherwise sorted and mixed in with everything else by score.

7. **Enrich.** For each item that passes quota:
   - **Concept extraction:** Identify 1–3 concepts, terms, methods, tools, organisations,
     or works referenced in the item that your audience may not already know (skip what
     is common knowledge in the configured field). Use WebSearch/WebFetch to look them up.
   - **Background:** Write 2–4 sentences of context that help a reader without deep domain expertise understand the news. Ground it in the search results; do not fabricate. If the item is self-explanatory, omit background entirely.
   - **Community discussion:** If the item carries comments in its metadata, write 1–3 sentences summarizing overall sentiment and key viewpoints — agreements, disagreements, concerns, and notable counterarguments. Omit if no comments exist.
   - **Market tag:** Tag every market-category item with `#market` (in addition to its topical tags) so market coverage is visually distinguishable in the feed.
   - Note 1–3 source URLs from your web search that you actually relied on.

8. **Write briefing (optional).** A plain-Markdown archive of the same content.
   The website and podcast are built from `issue.json` in step 10, so skip this step
   entirely unless you want the Markdown copies. Produce two files for today's date
   (UTC):
   - `<repo>/summaries/YYYY-MM-DD-en.md`
   - `<repo>/summaries/YYYY-MM-DD-zh.md`

   **English file layout (`-en.md`):**
   ```
   # Horizon Daily - YYYY-MM-DD

   > From {total_fetched} items, {N} important content pieces were selected.

   ---

   1. [Item title](#item-1) ⭐️ {score}/10
   2. [Item title](#item-2) ⭐️ {score}/10
   ...

   ---

   <a id="item-1"></a>
   ## [Title](url) ⭐️ {score}/10

   {whats_new — 1–2 sentences: what exactly happened, what changed. Be specific about names, versions, numbers, dates.}

   {why_it_matters — 1–2 sentences: significance, impact, who is affected, connection to broader trends.}

   {key_details — 1–2 sentences: notable specifics, limitations, or caveats the configured
   reader would value.}

   {source_type} · {sub_source / author} · {date/time} [· Discussion](discussion_url if different from url)

   **Background**: {2–4 sentences of background knowledge, or omit section if self-explanatory}

   <details><summary>References</summary>
   <ul>
   <li><a href="{url}">{title}</a></li>
   </ul>
   </details>

   **Discussion**: {1–3 sentence community discussion summary, or omit section if no comments}

   **Tags**: `#tag1`, `#tag2`, `#tag3`

   ---
   ```
   Repeat the item block for every selected item.

   **Chinese file layout (`-zh.md`):**  
   Same structure as EN but with all narrative text in Simplified Chinese （简体中文）. Use Pangu spacing （insert a space between CJK characters and adjacent ASCII letters/digits）. Keep technical abbreviations，acronyms，and widely-used proper nouns （e.g.，"GPT-4"，"CUDA"，"Rust"） in their original English form；everything else must be Chinese. Labels become：
   - Header：`Horizon 每日速递 - YYYY-MM-DD`
   - Lead：`从 {total_fetched} 条内容中筛选出 {N} 条重要资讯。`
   - **背景** （Background），**社区讨论** （Discussion），**参考链接** （References），**标签** （Tags）
   - Date format in source line：`{M}月{D}日 {HH:MM}`

   If no items pass filtering, write a single file (or both files) noting that no significant developments were found today.

## Publish the text edition (Phase 2)

After selecting and enriching items, emit a structured `issue.json` and render the site:

10. **Build `issue.json`.** For the selected items, produce an object:
    `{date, total_fetched, selected, overview:{summary:{en,zh}, bullets:[{en,zh}, ...]},
    items:[{id, url, score, source_type, sub_source,
    time:{en,zh}, title:{en,zh}, whats_new:{en,zh}, why_it_matters:{en,zh},
    key_details:{en,zh}, background:{en,zh}|null, discussion:{en,zh}|null, tags:[...],
    refs:[{label,url}]}]}`. `source_type` must be exactly `Hacker News`/`Reddit`/`RSS`/`GitHub`.
    `background`/`discussion` are `null` when absent.

    **`overview` is required** (it renders the "Today in brief" block at the top of the
    page). Write a `summary` (1–2 sentences, bilingual) capturing the day's throughline,
    plus 3–4 `bullets` (bilingual) calling out the most important developments. Ground it
    in the selected items; do not introduce items not in the briefing. If nothing passed
    filtering (see step 14), omit `overview` along with everything else.

    Write `issue.json` to the cloned `horizon-feed` working copy (temporary; not committed).

11. **Render.** Run:
    `cd <repo>/tools && <horizon venv>/python -c "import json,render; from pathlib import Path; render.write_issue(json.load(open('<site>/issue.json')), Path('templates'), Path('<site>'))"`
    This writes `index.html`, `YYYY-MM-DD.html`, `YYYY-MM-DD.meta.json`, and `archive.html`.

12. **Update dedup ledger.** Merge the `new_entries` from step 2 into `<site>/seen.json`
    and prune to 7 days using `tools/dedup.py`'s `prune_ledger(ledger, days=7)`. Write it back.

13. **Publish.** In the `<site>` clone:
    `git add -A && git commit -m "feed: <date>" && git push`. CF Pages auto-deploys.
    If `git push` fails, report the error and stop (the commit stays for the next run to retry).

14. **No items?** If nothing passes filtering, do NOT commit an empty issue — leave the
    previous one live and report "no new items".

## Podcast (Phase 3)

Runs AFTER the feed publish (step 13). Failures here must NOT block or revert the
text briefing — on any error, report it and stop the podcast phase; the next run
retries. Requires env: GEMINI_API_KEY, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
R2_SECRET_ACCESS_KEY (source `<repo>/.env`; show names and voices come from `config.toml`).

15. **Write two narration scripts** from issue.json, saved to
    `<site>/scripts/YYYY-MM-DD-zh.md` and `YYYY-MM-DD-en.md`.

    Style (both languages): single narrator, conversational news-anchor register,
    no host names, no "welcome back" filler. Structure:
    - Opening: show name + date + the day's throughline (from overview), ~30s.
    - Deep dives: every item with score >= 7, in score order — what happened,
      why it matters, key caveat. ~60-90s each.
    - Quick hits: remaining items, one sentence each.
    - Sign-off：one line （"明天见" / "See you tomorrow"）.

    Segments separated by `---` on its own line (one segment = opening or one deep
    dive). Use `***` for the one separator immediately BEFORE the quick-hits
    block (triggers the faster transition sound). Split the quick-hits block into
    sub-segments of <=300 chars (ZH) / <=150 words (EN), separated by `~~~`
    (silent boundary, no sound). Merge the sign-off line into the LAST quick-hits
    sub-segment — never make it its own segment. Segment length rules (they fight
    TTS drift): every segment 100-800 chars; nothing shorter than ~80 chars,
    nothing longer than ~800. Intro/outro/transition sounds are inserted
    automatically by podcast_tts.py from assets/audio/ — never write sound cues
    in the text.
    ZH：简体中文，Pangu spacing，keep technical terms/proper nouns in English，
    numbers read naturally. Target ZH 3000-4000 chars, EN 1200-1600 words.
    Do not read URLs aloud. No music/sfx cues — TTS reads everything literally.

16. **Synthesize.** For each lang:
    `cd <repo>/tools && ../horizon/.venv/bin/python podcast_tts.py <site>/scripts/YYYY-MM-DD-<lang>.md /tmp/YYYY-MM-DD-<lang>.mp3 <lang>`
    It prints `<path> <duration>s`. Sanity: duration 300-1200s.

17. **Upload.** For each mp3:
    `../horizon/.venv/bin/python podcast_upload.py /tmp/YYYY-MM-DD-<lang>.mp3`
    It prints `<public_url> <bytes>`. Only proceed to step 18 for episodes that
    uploaded successfully.

18. **Update episodes ledger.** Append one entry per uploaded episode to
    `<site>/episodes.json`:
    `{date, lang, title, description, mp3_url, bytes, duration_s}`.
    - `title`: `YYYY-MM-DD · <short headline>` (headline: the day's throughline,
      <=20 chars ZH / <=8 words EN, in the episode's language).
    - `description`: overview summary + numbered item list with source URLs
      (plain text, `\n` separated), in the episode's language.

19. **Regenerate feeds.**
    `../horizon/.venv/bin/python podcast_rss.py <site>`
    writes podcast-zh.xml and podcast-en.xml.

20. **Publish podcast artifacts.** In `<site>`:
    `git add episodes.json scripts/ podcast-zh.xml podcast-en.xml && git commit -m "podcast: <date>" && git push`.
    (Separate commit from the feed commit so a podcast failure never holds the
    briefing hostage.)

## Notes
- No external AI API key is used; scoring, deduplication, enrichment, and summarizing are your own reasoning.
- If fetch returns `[]`, write nothing and report "no new items".
- The `whats_new`, `why_it_matters`, and `key_details` fields (from the enrichment stage) replace the simple one-sentence `summary` produced during scoring — use the richer structured fields in the final briefing.
- Only include URLs in References that you actually retrieved from web search — never invent or modify URLs.
