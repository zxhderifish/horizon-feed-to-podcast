"""Synthesize a narration script to mp3 via Gemini TTS.

Script format: markdown, segments separated by `---` on its own line.
Each segment is synthesized separately (long-form drift avoidance), raw PCM
(24kHz s16le mono) is byte-concatenated, then encoded once with ffmpeg.
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import List

import config as app_config

# Model ids change. Override with GEMINI_TTS_MODEL; list what your key can see with:
#   python -c "from google import genai,os; \
#     print([m.name for m in genai.Client(api_key=os.environ['GEMINI_API_KEY']).models.list() if 'tts' in m.name])"
MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")
# Prebuilt Gemini voice per show, from config.toml.
SAMPLE_RATE = 24000

# Sound package: raw PCM (s16le 24kHz mono) matching TTS output, so assembly
# is plain byte concatenation. Missing files degrade to speech-only.
SFX_DIR = Path(__file__).resolve().parent.parent / "assets" / "audio"
SFX_NAMES = ("intro", "sting", "quickhits", "outro")


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


def load_sfx(sfx_dir: Path = SFX_DIR) -> dict:
    if not sfx_dir.is_dir():
        return {}
    out = {}
    for name in SFX_NAMES:
        p = sfx_dir / f"{name}.pcm"
        if p.exists():
            out[name] = p.read_bytes()
    return out


def assemble(seg_pcms: List[bytes], boundaries: List[str], sfx: dict) -> bytes:
    out = sfx.get("intro", b"")
    for i, pcm in enumerate(seg_pcms):
        if i:
            out += sfx.get(boundaries[i - 1], b"")
        out += pcm
    return out + sfx.get("outro", b"")


def synth_segment(client, text: str, voice: str) -> bytes:
    from google.genai import types

    resp = client.models.generate_content(
        model=MODEL,
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
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


def synthesize(script_path: Path, out_path: Path, lang: str) -> float:
    """Full pipeline: script file -> mp3. Returns duration in seconds."""
    segments, boundaries = parse_script(script_path.read_text(encoding="utf-8"))
    if not segments:
        raise ValueError(f"no segments in {script_path}")

    from google import genai

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    voice = app_config.get()["podcast"]["shows"][lang]["voice"]
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
    pcm_to_mp3(assemble(seg_pcms, boundaries, load_sfx()), out_path)
    return mp3_duration_s(out_path)


if __name__ == "__main__":
    shows = app_config.get()["podcast"]["shows"]
    if len(sys.argv) != 4 or sys.argv[3] not in shows:
        langs = "|".join(shows)
        print(f"usage: podcast_tts.py <script.md> <out.mp3> <{langs}>", file=sys.stderr)
        sys.exit(2)
    script, out, lang = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    dur = synthesize(script, out, lang)
    print(f"{out} {dur:.0f}s")
