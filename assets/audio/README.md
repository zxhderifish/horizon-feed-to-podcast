# Sound package (optional)

Episodes work fine without any of this. If you want an intro jingle and
transition sounds, drop raw PCM files in this directory and the pipeline picks
them up automatically:

| File | When it plays |
|---|---|
| `intro.pcm` | before the opening segment |
| `sting.pcm` | at each `---` boundary (between deep-dive items) |
| `quickhits.pcm` | at the `***` boundary (entering the quick-hits block) |
| `outro.pcm` | after the sign-off |

Missing files are skipped silently — add only the ones you want, or none.

## Format

Raw PCM, **s16le, 24 kHz, mono** — the same format Gemini TTS returns, so the
pipeline concatenates speech and sound as plain bytes with no resampling. To
convert something you already have:

```bash
ffmpeg -i intro.mp3 -f s16le -ar 24000 -ac 1 intro.pcm
```

To listen to a `.pcm` before committing to it:

```bash
ffmpeg -f s16le -ar 24000 -ac 1 -i intro.pcm intro.mp3
```

## Choosing sounds

Sound design belongs to your show, so nothing is shipped here — a jingle that
suits a chip-industry briefing is wrong for a fiction podcast. Whatever you
pick, two constraints matter in practice: keep the intro to about 3–5 seconds
and transitions under a second, and mix them a few dB below the narration or
they will fight the voice.

Source them however you like — a library you have rights to, a few seconds
recorded yourself, or synthesized from scratch (a short tone sequence written
with `math.sin` and packed via `struct` is about forty lines of Python and
avoids licensing questions entirely).
