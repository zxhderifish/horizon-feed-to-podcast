# Local TTS setup (Qwen3-TTS via vLLM-Omni)

Optional. The pipeline narrates with Gemini TTS out of the box and that path is
never removed — this doc adds a local model in front of it, with Gemini as the
automatic fallback.

The reason to bother is not cost. At one episode per language per day Gemini TTS
is cheap, and a GPU that idles the other 23 hours is not obviously cheaper. The
reasons that hold up are: no per-minute billing to think about, so episode
length and language count stop being budget decisions; no dependency on a
provider's free tier surviving the year; and nothing about your show's script
leaving your machine.

## What runs where

`tools/podcast_tts.py` sends one JSON request per language — the whole script,
all segments at once — to `tools/remote/synth_qwen.py`, which loads the model,
synthesizes every segment in a single batch, and writes framed PCM back. That is
a plain subprocess on the same machine, or one SSH call if the GPU lives
elsewhere.

The synthesizer is stateless: it starts, synthesizes, exits. Nothing to keep
running, nothing to restart after a reboot, no port to guard.

Each run prints `tts=local` or `tts=gemini (...)`. Best-of-N adds a summary such
as `tts=local (best-of-2: 3/12 swapped, 0 loud)`. **Read that line.** `asr=off`
or a non-zero `loud` count needs attention even though synthesis completed.

## Requirements

- **An NVIDIA GPU with ~9 GB of free VRAM.** Peak measured usage is 8.5 GiB.
- **~12 GB of disk**: about 4 GB of model weights into `~/.cache/huggingface`
  on first run, plus roughly 8 GB for the venv (vLLM and the CUDA wheels
  dominate).
- **Linux with a working CUDA driver.** `nvidia-smi` must report your card.
  WSL2 works; see the notes at the bottom.
- **Python 3.12 and [uv](https://docs.astral.sh/uv/).**

## Which model, and why this one

`Qwen/Qwen3-TTS-12Hz-1.7B-Base` (Apache-2.0). It was picked over the
alternatives on four counts, in a bake-off against a real episode:

- **Terminology accuracy.** Measured by transcribing the output and comparing
  against the script, it beat both Gemini and the other open contender on the
  words a technical show gets wrong most — Latin-script terms dropped into
  Chinese speech, and dense acronym runs in English.
- **Speed.** ~6.6 minutes of GPU time for ~22 minutes of audio, once every
  segment is submitted in one batch.
- **Format.** It emits 24 kHz mono, the same as Gemini TTS, so a sound package
  built for one works with the other unchanged. A 48 kHz stereo model would
  mean rebuilding your sound assets.
- **Licence.** Several better-ranked models on the TTS leaderboards are
  research-only weights. If you publish the audio, check the licence before
  the quality.

Two things worth knowing before you go looking for something bigger: the open
Qwen3-TTS weights stop at 1.7B and 12 Hz — the larger and higher-rate variants
are API-only — and Google ships no open TTS model, Gemma included.

## Step 1: build the TTS environment

vLLM is far too heavy to put in the pipeline's own venv. Give it a separate one.
On the machine with the GPU:

```bash
VLLM_OMNI_COMMIT=63ed0fdef4e6e9bdd866ea418ad9cf65e498c67a
mkdir -p ~/horizon-tts && cd ~/horizon-tts
uv venv --managed-python --python 3.12 .venv
uv pip install --python .venv/bin/python vllm==0.28.0 --torch-backend=auto
git clone -q https://github.com/vllm-project/vllm-omni.git
git -C vllm-omni checkout -q $VLLM_OMNI_COMMIT
uv pip install --python .venv/bin/python -e vllm-omni
mkdir -p vllm_omni_helpers
cp vllm-omni/examples/offline_inference/text_to_speech/qwen3_tts/end2end.py vllm_omni_helpers/
.venv/bin/python -c "import vllm, vllm_omni; print(vllm.__version__)"
```

Expected: prints `0.28.0`. A `RuntimeWarning` about a vLLM/vLLM-Omni version
mismatch is benign.

**Check the install log for `torch==*+cu*`, not `torch==*+cpu`.** If GPU
detection fails, `--torch-backend=auto` installs CPU-only torch and says
nothing. You find out much later, when synthesis is impossibly slow.

### `vllm_omni_helpers/end2end.py` is a pinned dependency

`synth_qwen.py` calls a private function (`_estimate_prompt_len`) out of an
upstream *example* file. That is why the clone above is pinned to a commit
rather than `--depth 1` of `main`: re-running this setup next year has to copy
the same helper.

If the file goes missing or upstream renames the function, `synth_qwen.py`
aborts naming the file and pointing back at this step, rather than raising a
bare `ImportError`. Re-copy it with the `cp` line above.

### Optional best-of-N judge

The default remains one take per segment, preserving the original ~9 GB GPU
requirement. On a larger card, install the optional ASR judge and ask for two
independent takes:

```bash
cd ~/horizon-tts
uv pip install --python .venv/bin/python faster-whisper
# in the pipeline's .env
TTS_TAKES=2
```

With `TTS_TAKES=2`, each segment is synthesized twice. A 0.5-second-frame RMS
gate rejects takes above 0.30; after Qwen releases the GPU, faster-whisper
`large-v3` transcribes both takes and the closer match wins. If whisper cannot
load, synthesis continues with the loudness gate alone and the marker says
`asr=off`.

This mode has been exercised on a 16 GB GPU. Its memory behavior on the 9 GB
minimum configuration has not been validated, so keep one take there. The
first judge run also downloads roughly 3 GB of whisper weights into the
Hugging Face cache.

## Step 2: make the voice reference clips

The local model does not have prebuilt voices. It clones one from a ~30 second
sample plus the exact text spoken in it. See
[`assets/voice/README.md`](../assets/voice/README.md) — it is one command, but
it spends Gemini quota and changes how every later episode sounds, so read it
first.

## Step 3: point the pipeline at it

**Same machine as the pipeline** — the common case. Tell it which interpreter
has vLLM:

```bash
TTS_PYTHON=~/horizon-tts/.venv/bin/python
```

That is all. `podcast_tts.py` runs `tools/remote/synth_qwen.py` as a subprocess
and reads `assets/voice/` straight out of the repo.

**GPU on another box** — add an SSH target:

```bash
TTS_REMOTE_HOST=gpu-box          # a Host from ~/.ssh/config, or user@host
TTS_REMOTE_DIR=~/horizon-tts     # optional; this is the default
```

Key-based login has to work unattended, and the remote directory must be the
one from Step 1. Every run scp's `synth_qwen.py` and `assets/voice/` across
first, so this repo stays the single source of truth and the two cannot drift.
`TTS_PYTHON` is ignored here; the remote's `.venv/bin/python` is used.

Then render one episode and listen to it before trusting it.

## Turning it off

```bash
TTS_LOCAL=0
```

Skips the local path entirely and goes straight to Gemini. Worth setting while
you are still deciding, since the default is local-first.

## What to expect

Measured on an RTX 5060 Ti (16 GB), both languages, ~13 segments each:

| | audio produced | wall clock |
|---|---|---|
| Chinese | 636 s | ~6 min |
| English | 564 s | ~6 min |

Model load dominates the fixed cost — a two-segment test still takes ~2.5
minutes, so a short episode is not proportionally faster. The current ceilings
are `LOCAL_BUDGET_S = 1000` and `ATTEMPT_TIMEOUT_S = 900`; they leave room for
two-take generation plus ASR. Raise them if your card is slower.

The local voice ran 3–9% faster-paced than Gemini's on identical scripts. That
is a difference to listen for, not an error.

## Troubleshooting

Start from the `tts=` value the run printed. Plain `tts=local` is the one-take
path. A best-of-N marker reports swaps and loud segments; `asr=off` means the
judge could not load and selection used loudness only. `tts=gemini (local
failed: ...)` carries a one-line reason; the full error is on stderr.

**Best-of-N says `asr=off`** — verify `faster-whisper` is installed in the same
TTS venv, then run one real episode again. A successful production-path check
must print `best-of-2` without `asr=off`; merely constructing `WhisperModel`
does not prove its CUDA encoder can run. If stderr names missing CUDA libraries,
install the CUDA 12 `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` wheels in that
venv; the synthesizer preloads their packaged libraries before importing
ctranslate2.

**`malformed TTS payload: bad header (...): 'INFO ...'`** — something wrote to
stdout ahead of the framed payload. vLLM's default log handler targets stdout,
which is why `synth_qwen.py`'s `__main__` saves fd 1, points fd 1 at stderr, and
writes the payload only to the saved descriptor. If this recurs, the header
snippet in the error names the culprit. Do not "fix" it with
`VLLM_CONFIGURE_LOGGING=0` or `contextlib.redirect_stdout` — both cover only a
subset of the writers, missing C extensions and forked children.

**`cannot recover segment order from request ids ...`** or **`unexpected request
id format ...`** — vllm-omni changed how it names requests. It assigns
`f"{index}_{uuid4()}"` and `order_request_ids` parses the prefix. The
permutation check is deliberate: a format change must fail loudly rather than
emit a plausible-sounding but scrambled episode. Check the pinned
`VLLM_OMNI_COMMIT` against what is on the box.

**`missing remote helper .../end2end.py`** or **`has no _estimate_prompt_len`**
— see the pinned-dependency section in Step 1.

**`attempt 1 timeout after 600s`** — the synthesizer accepted the work and went
quiet. The run does **not** retry after a timeout: the orphaned process still
holds VRAM, so a second attempt would land on an occupied card. Let it exit on
its own. Fast failures (box off, connection refused, vLLM crash) still get all
three attempts.

**Out of memory, or the process dies with no traceback.** Between roughly 13.75
and 15.9 GiB there is a cliff where the kernel kills the process outright —
`OutOfMemoryError` never gets raised, so no amount of exception handling helps.
The batch size is not tunable from here by design; if you are adapting this for
a smaller card, shrink it in `synth_qwen.py` rather than adding a fallback that
cannot fire.

**Never `kill -9` a process doing CUDA work.** It can wedge the GPU driver, and
recovery is a reboot. If a call times out client-side, the synthesizer may still
be running — poll `nvidia-smi` and let it finish.

## Notes for WSL2

Everything above works under WSL2, with four wrinkles:

- **`nvidia-smi` is not on the default PATH.** Export
  `PATH=/usr/lib/wsl/lib:$HOME/.local/bin:$PATH` before *any* command that
  needs it — including `uv pip install`, which uses it to decide whether to
  install CUDA torch. This is the most common cause of a silent `+cpu` install.
- **WSL must be running before the pipeline connects**, if you are driving it
  over SSH from another machine. A Windows reboot leaves it down until
  something starts it.
- **`sshd` inside WSL** is what the SSH path talks to; confirm with
  `systemctl is-active ssh`.
- **If the GPU wedges**, recovery is `wsl --shutdown` from Windows
  (PowerShell/cmd). There is no in-WSL command that un-wedges the driver, and
  it cannot be scripted from inside an SSH session into WSL.
