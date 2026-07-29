# Cloudflare setup

This pipeline needs two things hosted: **the episode audio** and **the site plus
RSS feeds**. Cloudflare covers both on its free tier, and R2 charges nothing for
egress — which matters, because every podcast client that downloads an episode
is egress.

| What | Cloudflare product | Serves |
|---|---|---|
| Episode mp3s | **R2** bucket + custom domain | `podcast.example.com/2026-07-28-en.mp3` |
| Site, RSS, cover art | **Pages** project connected to your publishing repo | `feed.example.com/podcast-en.xml` |

Nothing here is Cloudflare-specific in principle: R2 speaks the S3 API, so any
S3-compatible store works with the same credentials layout, and any static host
can replace Pages. The steps below are just the path this project was built on.

**Before you start:** add your domain to Cloudflare (Dashboard → *Add a site*,
then point your registrar at Cloudflare's nameservers). Both custom domains below
assume the zone is already active — otherwise you will be issuing DNS records by
hand and waiting on propagation.

---

## Part 1 — R2 bucket for episode audio

### 1. Create the bucket

Dashboard → **R2** → *Create bucket*.

- **Name**: whatever you like (e.g. `my-podcast-audio`). This goes in
  `config.toml` as `hosting.bucket`.
- **Location**: *Automatic* is right unless you have a reason otherwise.
- **Storage class**: *Standard*. Do not use Infrequent Access — podcast episodes
  are read often and IA adds retrieval fees.

Leave everything else default. The bucket is private at this point, which is
correct.

### 2. Bind a custom domain

Bucket → **Settings** → *Custom Domains* → *Connect Domain* →
`podcast.example.com`.

This is the step that makes objects publicly readable, and it is the one people
get wrong. Two ways exist to expose R2:

- **Custom domain** (use this): objects are served from your own hostname, cached
  at Cloudflare's edge, and the URL is stable and brandable.
- **Public development URL** (`pub-<hash>.r2.dev`): rate-limited, not meant for
  production, and ugly in a public RSS feed. Skip it.

Cloudflare creates the DNS record for you when the zone is on the same account.
Give it a minute, then confirm the domain shows as *Active*.

### 3. Create a scoped API token

Dashboard → **R2** → *API* → *Manage API Tokens* → *Create API Token*.

- **Permission**: **Object Read & Write**. Not Admin — the pipeline only needs to
  put and delete objects, and a narrower token limits the damage if it leaks.
- **Specify bucket**: select only your podcast bucket. A token that can reach
  every bucket in the account is a token you will regret.
- **TTL**: leave it non-expiring, or set a reminder to rotate it.

The result page shows three values **once**:

| Shown as | Goes in `.env` as |
|---|---|
| Access Key ID | `R2_ACCESS_KEY_ID` |
| Secret Access Key | `R2_SECRET_ACCESS_KEY` |
| (Account ID — also in the sidebar / the endpoint URL) | `R2_ACCOUNT_ID` |

It also shows a *Token value* (`cfat_…`) for Cloudflare's own REST API. This
pipeline uses the S3 API and does **not** need it.

The S3 endpoint is derived, not configured:
`https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com`, with region `auto`. That is
already built into `tools/podcast_upload.py`.

### 4. Fill in the config

`.env` (never committed):

```
R2_ACCOUNT_ID=your-32-hex-account-id
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
```

`config.toml`:

```toml
[hosting]
audio_url = "https://podcast.example.com"
bucket = "my-podcast-audio"
```

### 5. Verify before trusting it

```bash
set -a && source .env && set +a
cd tools
printf 'test' > /tmp/probe.mp3
python podcast_upload.py /tmp/probe.mp3          # prints the public URL + bytes
curl -sI https://podcast.example.com/probe.mp3   # expect 200, content-type audio/mpeg
```

If the upload succeeds but the URL 404s, the custom domain is not bound (or not
active yet) — the credentials are fine and the object is really there. Clean up
afterwards:

```bash
python -c "import podcast_upload; podcast_upload._client().delete_object(
    Bucket='my-podcast-audio', Key='probe.mp3')"
```

---

## Part 2 — Pages project for the site and feeds

### 1. Create the publishing repo

This is a **second** git repo, separate from this one: code lives here, published
output lives there. Create an empty repo (private is fine — Pages can read it
either way) and note its name.

The pipeline writes into it: `index.html`, `YYYY-MM-DD.html`, `archive.html`,
`podcast-<lang>.xml`, `episodes.json`, `seen.json`, `covers/`, `scripts/`.

### 2. Connect it to Pages

Dashboard → **Workers & Pages** → *Create* → **Pages** → *Connect to Git* →
pick the repo.

Build settings — the output is already static, so there is nothing to build:

| Field | Value |
|---|---|
| Framework preset | *None* |
| Build command | leave empty |
| Build output directory | `/` |
| Production branch | `master` or `main`, matching the repo |

Save and deploy. The first deploy may be empty if the repo is empty; that is
fine.

### 3. Add the custom domain

Pages project → **Custom domains** → *Set up a domain* → `feed.example.com`.

Then in `config.toml`:

```toml
[hosting]
site_url = "https://feed.example.com"

[site]
brand = "feed.example.com"
brand_html = 'feed<span class="dot">.</span>example.com'
```

Every other URL — cover art, each feed — is derived from `site_url`, so this is
the only place the domain appears.

### 4. Verify

After the pipeline's first run pushes to the publishing repo:

```bash
curl -sI https://feed.example.com/podcast-en.xml   # 200, content-type application/xml
xmllint --noout <(curl -s https://feed.example.com/podcast-en.xml) && echo "valid XML"
```

A deploy takes roughly 30–60 seconds after the push. During propagation you may
see a `307` that resolves to `200` on redirect-following (`curl -L`) — that is
normal, not a misconfiguration.

---

## Gotchas worth knowing in advance

**Deleted objects can still return 200.** Cloudflare's edge caches R2 responses.
Delete an object and `curl` may keep serving it from cache for a while. The
object really is gone; if you need the URL to 404 immediately, purge the cache
(Dashboard → *Caching* → *Configuration* → *Purge Everything*, or purge that
single URL). This matters most when replacing a file under the same key — assume
listeners may get the old bytes for a short window.

**Content-Type must be set explicitly on upload.** R2 does not guess from the
file extension. `podcast_upload.py` sets `audio/mpeg`, which podcast clients
require for enclosures; anything you upload by other means (cover art, for
instance) needs its own correct type or browsers will download it instead of
displaying it.

**Range requests just work.** Podcast apps seek within episodes using HTTP range
requests. R2 supports them natively — no configuration, but worth knowing so you
do not go looking for a setting.

**No CORS configuration needed.** Feeds and audio are fetched by podcast clients
and servers, not browser JavaScript, so the default (no CORS rules) is fine.
Only add rules if you build a web player that reads the audio cross-origin.

**Keep the token out of the repo.** `.env` is gitignored here. If a token does
leak, revoke it in *Manage API Tokens* and issue a new one — the bucket contents
are unaffected.

**One bucket, one purpose.** Resist putting unrelated files in the podcast
bucket. It is publicly readable through the custom domain, so anything you drop
there is world-readable at a guessable URL.

---

## What this costs

Free-tier allowances as of writing — Cloudflare adjusts these, so check the
current [R2](https://developers.cloudflare.com/r2/pricing/) and
[Pages](https://developers.cloudflare.com/pages/platform/limits/) pricing pages
before assuming:

- **R2**: 10 GB-month of storage, 1M Class A (write) operations, 10M Class B
  (read) operations, and **zero egress fees**.
- **Pages**: 500 builds per month, unlimited requests and bandwidth on the free
  plan.

For two ~10-minute episodes a day at roughly 5 MB each, that is about 11 MB/day —
around 4 GB a year, so storage stays inside the free tier for well over a year
even with no cleanup. Writes are a handful per day against a 1M allowance. Two
Pages deploys a day is ~60 builds a month against 500.

Egress is the line item that would sink this on most other hosts: a modestly
successful podcast serves far more download traffic than storage, and R2 charges
nothing for it.

When storage does eventually approach the limit, delete the oldest episode
objects (and drop their entries from `episodes.json`) — or move them to a cheaper
class if you want to keep the archive complete.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Upload succeeds, public URL 404s | Custom domain not bound, or not yet *Active* |
| `SignatureDoesNotMatch` | Wrong secret, or `R2_ACCOUNT_ID` from a different account |
| `NoSuchBucket` | `config.toml` bucket name does not match R2, or the token is scoped to a different bucket |
| `AccessDenied` on upload | Token is read-only — reissue with *Object Read & Write* |
| Feed URL 404s after push | Pages deploy still running, or *Build output directory* is not `/` |
| Feed URL serves an old version | Edge cache; purge the URL |
| Podcast client will not play an episode | Content-Type is not `audio/mpeg`, or the enclosure URL is not publicly reachable |
| Apple rejects the feed on submission | Fetch the feed yourself first — cover art missing, `itunes:owner` email absent, or the enclosure returning non-200 |
