"""Synthesize a narration script to mp3, local model first, Gemini TTS second.

Script format: markdown, segments separated by `---` on its own line.
Each segment is synthesized separately (long-form drift avoidance), raw PCM
(24kHz s16le mono) is concatenated with a guaranteed quiet lead and tail per
segment (see MIN_LEAD_MS, MIN_TAIL_MS, MAX_QUIET_MS), then encoded once with
ffmpeg.

The local path shells out to `tools/remote/synth_qwen.py` — on this machine by
default, or over SSH when TTS_REMOTE_HOST is set. Setup: docs/local-tts-setup.md.
Set TTS_LOCAL=0 to skip it entirely and use Gemini, which is what happens
anyway if no local model answers.
"""

import json
import os
import re
import subprocess
import sys
import time
from array import array
from pathlib import Path
from typing import List

import config as app_config

# Model ids change. Override with GEMINI_TTS_MODEL; list what your key can see with:
#   python -c "from google import genai,os; \
#     print([m.name for m in genai.Client(api_key=os.environ['GEMINI_API_KEY']).models.list() if 'tts' in m.name])"
MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")
# Prebuilt Gemini voice per show, from config.toml.
SAMPLE_RATE = 24000
GEMINI_CALL_TIMEOUT_S = 120

# Every speech segment is guaranteed this much quiet before whatever follows
# it. Gemini's segments ended with 157-436ms of silence and the show's rhythm
# came from that; Qwen3-TTS ends 17-142ms after the speech, so a sting landed
# on top of a still-sounding vowel and popped. 250ms sits at Gemini's own
# floor, so already-quiet segments (the whole Gemini path) pass through
# untouched. Engine-agnostic on purpose: a third synthesizer inherits it.
MIN_TAIL_MS = 250
# Mirror of MIN_TAIL_MS from the leading edge. Qwen3-TTS starts speaking
# immediately — a real episode jumped from digital silence straight to
# RMS 1754 at a segment's first sample — where Gemini's segments open with
# 168-363ms of hush (median 295). 250ms sits inside that floor, so it is
# inaudible on the Gemini path and already-quiet local segments still pass
# through untouched, same guarantee as the tail.
MIN_LEAD_MS = 250
# The other side of the same rule: the guarantee is a band, not just a floor.
# With the synthesized reference clip Qwen produces 243-652ms of leading quiet
# on its own and the show dragged, so quiet past this comes off. 350 is the top
# of Gemini's own lead band (168-363), i.e. inside the rhythm the show already
# has, and a no-op on any segment that is already inside it.
MAX_QUIET_MS = 350
FADE_MS = 15  # short enough to be inaudible on a decaying vowel
QUIET_LEVEL = 200  # below this nothing is audible at these levels

# Sound package: raw PCM (s16le 24kHz mono) matching TTS output, so assembly
# is plain byte concatenation. Missing files degrade to speech-only.
SFX_DIR = Path(__file__).resolve().parent.parent / "assets" / "audio"
SFX_NAMES = ("intro", "sting", "quickhits", "outro")

REPO_ROOT = Path(__file__).resolve().parent.parent
VOICE_DIR = REPO_ROOT / "assets" / "voice"
LOCAL_SCRIPT = Path(__file__).resolve().parent / "remote" / "synth_qwen.py"

# Unset means the GPU is on this machine and the local path is a plain
# subprocess. Set it to an ssh target (a Host from ~/.ssh/config, or
# user@host) to put the model on another box.
REMOTE_HOST = os.environ.get("TTS_REMOTE_HOST", "")
REMOTE_DIR = os.environ.get("TTS_REMOTE_DIR", "~/horizon-tts")
# The interpreter that has vllm + vllm-omni installed. vLLM is far too heavy a
# dependency to put in the pipeline's own venv, so on the same-machine path
# this is nearly always a second venv and must be set; sys.executable is only a
# default so the misconfiguration surfaces as a clean import error.
LOCAL_PYTHON = os.environ.get("TTS_PYTHON", sys.executable)
SSH_CONNECT_TIMEOUT_S = 15
# ConnectTimeout only bounds the handshake. A box that accepts the connection
# and then wedges (has happened: docs/local-tts-setup.md) leaves ssh waiting
# on a dead TCP session forever, so ask it to prove liveness every 15s.
SSH_OPTS = ["-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT_S}",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4"]


def shows() -> dict:
    return app_config.get()["podcast"]["shows"]


def voice_for(lang: str) -> str:
    return shows()[lang]["voice"]


def parse_script(md: str):
    """Split on separator lines. `---` -> sting, `***` -> quickhits, `~~~` -> silent.

    Returns (segments, boundaries) with len(boundaries) == len(segments) - 1.
    Leading/trailing separators are ignored; between two segments the last
    separator seen wins.
    """
    tokens = re.split(r"(?m)^[ \t]*(---|\*\*\*|~~~)[ \t]*$", md)
    segments: List[str] = []
    boundaries: List[str] = []
    pending = None
    _KIND = {"***": "quickhits", "~~~": "none"}
    for i, tok in enumerate(tokens):
        if i % 2 == 1:  # separator token (captured group)
            pending = _KIND.get(tok, "sting")
        else:
            s = tok.strip()
            if s:
                if segments:
                    boundaries.append(pending or "sting")
                segments.append(s)
                pending = None
    return segments, boundaries


def split_script(md: str) -> List[str]:
    return parse_script(md)[0]


def unframe_pcms(blob: bytes) -> tuple:
    """Inverse of synth_qwen.frame_pcms. Returns (segments, sample_rate)."""
    nl = blob.find(b"\n")
    if nl < 0:
        raise ValueError("malformed TTS payload: no header line")
    raw_head = blob[:nl]
    try:
        head = json.loads(raw_head.decode("utf-8"))
        lens, sr = head["lens"], head["sr"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as e:
        # e.g. a vLLM log line that leaked onto fd 1 ahead of the payload —
        # show the operator what actually arrived, capped so a wall of log
        # spew (or raw PCM masquerading as a header) can't flood the terminal.
        snippet = raw_head[:200].decode("utf-8", "replace")
        raise ValueError(f"malformed TTS payload: bad header ({e!r}): {snippet!r}") from e
    if not isinstance(lens, list) or not all(
        isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in lens
    ):
        raise ValueError(f"malformed TTS payload: bad lens {repr(lens)[:200]}")
    body, pos, out = blob[nl + 1:], 0, []
    for n in lens:
        chunk = body[pos:pos + n]
        if len(chunk) != n:
            raise ValueError("malformed TTS payload: truncated body")
        out.append(chunk)
        pos += n
    if pos != len(body):
        raise ValueError("malformed TTS payload: trailing bytes")
    return out, int(sr)


def load_sfx(sfx_dir: Path = SFX_DIR) -> dict:
    if not sfx_dir.is_dir():
        return {}
    out = {}
    for name in SFX_NAMES:
        p = sfx_dir / f"{name}.pcm"
        if p.exists():
            out[name] = p.read_bytes()
    return out


# PCM is s16le and array("h") is native-endian, i.e. the same on every host
# this runs on; `usable` guards the half sample a truncated blob would leave.
def _fade_out(pcm: bytes) -> bytes:
    """Linear ramp over the last FADE_MS, so padding does not just move the
    step it is meant to remove: zeros after full-level speech click too."""
    usable = len(pcm) // 2 * 2
    n = min(FADE_MS * SAMPLE_RATE // 1000, usable // 2)
    if not n:
        return pcm
    cut = usable - 2 * n
    tail = array("h", pcm[cut:usable])
    for i in range(n):
        tail[i] = tail[i] * (n - 1 - i) // n
    return pcm[:cut] + tail.tobytes() + pcm[usable:]


def _tail_quiet_run(pcm: bytes) -> int:
    """Samples of inaudible run at the end. Counted to its true length, not
    to a threshold: the cap has to know how far past itself the run goes.
    Costs nothing on real input — the loop stops at the first audible
    sample."""
    usable = len(pcm) // 2 * 2
    n = 0
    for s in reversed(array("h", pcm[:usable])):
        if abs(s) >= QUIET_LEVEL:
            break
        n += 1
    return n


def _quiet_tail(pcm: bytes) -> bytes:
    """Bring `pcm`'s trailing quiet inside [MIN_TAIL_MS, MAX_QUIET_MS]: pad a
    short one (fading first), trim an over-long one. A segment already inside
    the band is returned byte-for-byte unchanged."""
    want = MIN_TAIL_MS * SAMPLE_RATE // 1000
    cap = MAX_QUIET_MS * SAMPLE_RATE // 1000
    usable = len(pcm) // 2 * 2
    quiet = _tail_quiet_run(pcm)
    if quiet > cap:
        # Only silence is dropped and both sides of the cut are silence, so
        # unlike the pad this needs no fade.
        return pcm[:usable - 2 * (quiet - cap)] + pcm[usable:]
    if quiet >= want:
        return pcm
    return _fade_out(pcm) + b"\x00\x00" * (want - quiet)


def _fade_in(pcm: bytes) -> bytes:
    """Linear ramp over the first FADE_MS, mirror of _fade_out: a pad's
    silence butted against full-level speech is a click from the other
    side too."""
    usable = len(pcm) // 2 * 2
    n = min(FADE_MS * SAMPLE_RATE // 1000, usable // 2)
    if not n:
        return pcm
    head = array("h", pcm[:2 * n])
    for i in range(n):
        head[i] = head[i] * i // n
    return head.tobytes() + pcm[2 * n:usable] + pcm[usable:]


def _lead_quiet_run(pcm: bytes) -> int:
    """Mirror of _tail_quiet_run from the head."""
    n = 0
    for s in array("h", pcm[:len(pcm) // 2 * 2]):
        if abs(s) >= QUIET_LEVEL:
            break
        n += 1
    return n


def _quiet_lead(pcm: bytes) -> bytes:
    """Bring `pcm`'s leading quiet inside the band, fading the speech in when
    padding — same rule as _quiet_tail, from the other end."""
    want = MIN_LEAD_MS * SAMPLE_RATE // 1000
    cap = MAX_QUIET_MS * SAMPLE_RATE // 1000
    quiet = _lead_quiet_run(pcm)
    if quiet > cap:
        return pcm[2 * (quiet - cap):]
    if quiet >= want:
        return pcm
    return b"\x00\x00" * (want - quiet) + _fade_in(pcm)


def assemble(seg_pcms: List[bytes], boundaries: List[str], sfx: dict) -> bytes:
    out = sfx.get("intro", b"")
    for i, pcm in enumerate(seg_pcms):
        if i:
            out += sfx.get(boundaries[i - 1], b"")
        out += _quiet_lead(_quiet_tail(pcm))
    return out + sfx.get("outro", b"")


def synth_segment(client, text: str, voice: str) -> bytes:
    from google.genai import types

    resp = client.models.generate_content(
        model=MODEL,
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            http_options=types.HttpOptions(timeout=GEMINI_CALL_TIMEOUT_S * 1000),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                )
            ),
        ),
    )
    inline = resp.candidates[0].content.parts[0].inline_data
    if inline.data is None:
        raise RuntimeError("no audio data in response (safety block or text part?)")
    mime = inline.mime_type or ""
    if "rate=" in mime and f"rate={SAMPLE_RATE}" not in mime:
        raise RuntimeError(f"unexpected TTS audio format: {mime}")
    return inline.data


def _gemini_client():
    from google import genai

    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _synth_all_gemini(segments: List[str], lang: str) -> List[bytes]:
    """The original per-segment loop, unchanged apart from being callable alone."""
    client = _gemini_client()
    voice = voice_for(lang)
    seg_pcms: List[bytes] = []
    for i, seg in enumerate(segments):
        for attempt in (1, 2):  # retry once per segment
            try:
                seg_pcms.append(synth_segment(client, seg, voice))
                break
            except Exception as e:
                if attempt == 2:
                    raise RuntimeError(f"segment {i} failed twice: {e}") from e
                time.sleep(10)
        time.sleep(2)  # be gentle with rate limits
    return seg_pcms


def _left(deadline: float, what: str) -> float:
    """Time still on the attempt's clock, or a TimeoutExpired indistinguishable
    from one raised by subprocess itself — the caller has one timeout branch."""
    r = deadline - time.monotonic()
    if r <= 0:
        raise subprocess.TimeoutExpired(cmd=what, timeout=0)
    return r


def _sync_remote_files(deadline: float) -> None:
    """Push the synthesizer and voice assets to the remote box. This repo is
    the single source of truth; the box holds no checkout, so the two cannot
    drift (the lone exception is vllm_omni_helpers/end2end.py — see
    docs/local-tts-setup.md).

    Every call is bounded by the attempt's deadline: an untimed scp against a
    wedged-but-reachable box hangs the daily run forever, which is not a
    failure and so never reaches the retry or the Gemini fallback.
    """
    _run(["ssh", *SSH_OPTS, REMOTE_HOST, f"mkdir -p {REMOTE_DIR}/assets/voice"],
         timeout=_left(deadline, "ssh"))
    _run(["scp", "-q", *SSH_OPTS, str(LOCAL_SCRIPT), f"{REMOTE_HOST}:{REMOTE_DIR}/"],
         timeout=_left(deadline, "scp"))
    _run(["scp", "-q", *SSH_OPTS, *[str(p) for p in sorted(VOICE_DIR.iterdir())],
          f"{REMOTE_HOST}:{REMOTE_DIR}/assets/voice/"],
         timeout=_left(deadline, "scp"))


def _tts_command() -> tuple:
    """(argv, env) for one synthesis run. Same contract either way: JSON on
    stdin, framed PCM on stdout."""
    if REMOTE_HOST:
        return (["ssh", *SSH_OPTS, REMOTE_HOST,
                 f"{REMOTE_DIR}/.venv/bin/python {REMOTE_DIR}/synth_qwen.py"], None)
    # Same machine: nothing to sync, but synth_qwen.py now sits in tools/remote/
    # rather than beside the assets, so point it at the repo root.
    env = dict(os.environ, TTS_ASSET_DIR=str(REPO_ROOT))
    return ([LOCAL_PYTHON, str(LOCAL_SCRIPT)], env)


def _run_synth(payload: bytes, timeout_s: float) -> bytes:
    """One call: JSON in, framed PCM out. The only place that shells out."""
    argv, env = _tts_command()
    proc = subprocess.run(
        argv, input=payload, capture_output=True, timeout=timeout_s, env=env,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()[-500:]
        raise RuntimeError(f"local TTS failed (exit {proc.returncode}): {err}")
    return proc.stdout


def _synth_all_local(segments: List[str], lang: str, timeout_s: float) -> List[bytes]:
    """Sync and synthesis share one deadline, so an attempt costs at most what
    the caller granted — that is what makes LOCAL_BUDGET_S a ceiling by
    construction rather than an empirical hope."""
    deadline = time.monotonic() + timeout_s
    if REMOTE_HOST:
        _sync_remote_files(deadline)
    payload = json.dumps({"lang": lang, "segments": segments}).encode("utf-8")
    pcms, sr = unframe_pcms(_run_synth(payload, _left(deadline, "tts")))
    if sr != SAMPLE_RATE:
        raise RuntimeError(f"local TTS sample rate {sr}, expected {SAMPLE_RATE}")
    if len(pcms) != len(segments):
        raise RuntimeError(f"expected {len(segments)} segments, got {len(pcms)}")
    return pcms


LOCAL_ATTEMPTS = 3
ATTEMPT_TIMEOUT_S = 600
RETRY_BACKOFF_S = 30
# Hard ceiling on total local wall time per invocation. The CLI runs one
# process per language, so there is no cross-process state to share a single
# budget; size this against your own measured synthesis time.
LOCAL_BUDGET_S = 750


def _one_line(reason: str, cap: int = 180) -> str:
    """The tts= marker is meant to be copied into a run report, so it has to
    stay one readable line. The full reason is already on stderr."""
    flat = " ".join(str(reason).split())
    return flat if len(flat) <= cap else flat[:cap - 3] + "..."


def synth_all(segments: List[str], lang: str) -> tuple:
    """Synthesize every segment. Returns (pcms, path_used).

    Local model first, Gemini as fallback. Languages are independent: zh may
    run locally while en falls back.
    """
    if os.environ.get("TTS_LOCAL", "1") == "0":
        return _synth_all_gemini(segments, lang), "gemini (disabled)"

    started = time.monotonic()
    last = "unknown"
    for attempt in range(1, LOCAL_ATTEMPTS + 1):
        remaining = LOCAL_BUDGET_S - (time.monotonic() - started)
        # Bound each attempt by what is left, so the budget is a real ceiling
        # rather than a post-hoc check: 3 x ATTEMPT_TIMEOUT_S cannot fit in it.
        if remaining <= SSH_CONNECT_TIMEOUT_S:
            last = f"local budget of {LOCAL_BUDGET_S}s spent; {last}"
            break
        granted = min(ATTEMPT_TIMEOUT_S, remaining)
        try:
            return _synth_all_local(segments, lang, granted), "local"
        except subprocess.TimeoutExpired:
            # A hang leaves the synthesizer orphaned and still holding VRAM,
            # so a retry lands on an occupied card and is likelier to be
            # kernel-killed than to succeed. Fall back to Gemini now; fast
            # failures below still get all three attempts.
            last = f"attempt {attempt} timeout after {granted:.0f}s"
            print(f"[tts] local failed, {last}", file=sys.stderr)
            break
        except Exception as e:
            last = f"attempt {attempt}: {e}"
        print(f"[tts] local failed, {last}", file=sys.stderr)
        if attempt < LOCAL_ATTEMPTS:
            time.sleep(min(RETRY_BACKOFF_S,
                           max(0.0, LOCAL_BUDGET_S - (time.monotonic() - started))))
    return _synth_all_gemini(segments, lang), f"gemini (local failed: {_one_line(last)})"


def _run(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run wrapper that surfaces stderr in the exception message."""
    try:
        return subprocess.run(cmd, check=True, capture_output=True, **kwargs)
    except subprocess.CalledProcessError as e:
        err = e.stderr or b""
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        raise RuntimeError(
            f"{cmd[0]} failed (exit {e.returncode}): {err.strip()[-500:]}"
        ) from e


def pcm_to_mp3(pcm: bytes, out_path: Path) -> None:
    _run(
        [
            "ffmpeg", "-y", "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1",
            "-i", "pipe:0", "-b:a", "96k", str(out_path),
        ],
        input=pcm,
    )


def mp3_duration_s(path: Path) -> float:
    out = _run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        text=True,
    )
    return float(out.stdout.strip())


def synthesize(script_path: Path, out_path: Path, lang: str) -> tuple:
    """Full pipeline: script file -> mp3. Returns (duration_s, tts_path)."""
    segments, boundaries = parse_script(script_path.read_text(encoding="utf-8"))
    if not segments:
        raise ValueError(f"no segments in {script_path}")

    seg_pcms, path_used = synth_all(segments, lang)
    pcm_to_mp3(assemble(seg_pcms, boundaries, load_sfx()), out_path)
    return mp3_duration_s(out_path), path_used


if __name__ == "__main__":
    _shows = shows()
    if len(sys.argv) != 4 or sys.argv[3] not in _shows:
        langs = "|".join(_shows)
        print(f"usage: podcast_tts.py <script.md> <out.mp3> <{langs}>", file=sys.stderr)
        sys.exit(2)
    script, out, lang = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    dur, path_used = synthesize(script, out, lang)
    print(f"{out} {dur:.0f}s tts={path_used}")
