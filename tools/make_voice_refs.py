"""Regenerate assets/voice/<lang>-ref.wav from ref.json's text via Gemini TTS.

The reference clips a local model clones from are not cut from a published
episode: they are Gemini reading each language's ref text with the show's own
model and prebuilt voice, so the (audio, text) pair is exact by construction.
Cutting a clip out of a finished episode instead means transcribing it, and a
transcript that mishears one word degrades the clone.

This is the committed form of that one-shot procedure. The pipeline never
imports it — running it is always a deliberate act.

Costs real Gemini TTS quota (~30 s of audio per language) and changes the
cloned voice, so every later episode sounds different.
"""

import json
import sys
import wave
from pathlib import Path
from typing import List

import podcast_tts

USAGE = (
    "usage: make_voice_refs.py --yes-spend-quota [lang ...]\n"
    "  Overwrites the voice reference clips in assets/voice/.\n"
    "  SPENDS REAL GEMINI TTS QUOTA: ~30 s of audio per language.\n"
    "  Also changes the cloned voice: re-render and re-listen afterwards."
)


def write_wav(path: Path, pcm: bytes) -> float:
    """s16le mono at SAMPLE_RATE — the only format vLLM-Omni reads the ref
    clip in. Returns the clip's duration in seconds."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(podcast_tts.SAMPLE_RATE)
        w.writeframes(pcm)
    return len(pcm) // 2 / podcast_tts.SAMPLE_RATE


def regenerate(langs: List[str], client=None, voice_dir: Path = None) -> dict:
    """Synthesize each lang's ref text and rewrite its clip + ref.json entry.
    `client` is injectable so the wiring can be tested without spending quota."""
    voice_dir = voice_dir or podcast_tts.VOICE_DIR
    ref_json = voice_dir / "ref.json"
    meta = json.loads(ref_json.read_text(encoding="utf-8"))
    client = client if client is not None else podcast_tts._gemini_client()
    for lang in langs:
        m = meta[lang]
        voice = podcast_tts.voice_for(lang)
        pcm = podcast_tts.synth_segment(client, m["text"], voice)
        dur = write_wav(voice_dir / f"{lang}-ref.wav", pcm)
        # Keep the metadata honest about what actually produced these bytes.
        m.update(source="gemini-tts", model=podcast_tts.MODEL, voice=voice,
                 duration_s=round(dur, 2))
    ref_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return meta


if __name__ == "__main__":
    args = sys.argv[1:]
    shows = podcast_tts.shows()
    # The confirmation flag is required, not defaulted: being run must never
    # be enough to overwrite an approved voice.
    langs = args[1:] or list(shows)
    if not args or args[0] != "--yes-spend-quota" or any(
            lang not in shows for lang in langs):
        print(USAGE, file=sys.stderr)
        sys.exit(2)
    meta = regenerate(langs)
    for lang in langs:
        print(f"{lang}-ref.wav {meta[lang]['duration_s']:.2f}s "
              f"voice={meta[lang]['voice']}")
