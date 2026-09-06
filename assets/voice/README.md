# Voice reference clips (local TTS only)

Empty on purpose. Nothing here is needed for the Gemini path — it uses prebuilt
voices named in `config.toml`. These files exist only for the local model
(`docs/local-tts-setup.md`), which clones a voice from a sample instead.

A clone needs **audio plus the exact words spoken in it**. Get that pairing
wrong and fidelity drops, which is why these clips are generated rather than
cut out of a finished episode: transcribing your own audio introduces exactly
the errors you are trying to avoid.

## Generating them

1. Copy `ref.example.json` to `ref.json` and rewrite each language's `text` —
   roughly 30 seconds read aloud, in your show's own voice and vocabulary.
   Include the terms your show says constantly; the clone inherits how they
   are pronounced.
2. Run it once, per the warning it prints:

```bash
python tools/make_voice_refs.py --yes-spend-quota
```

That writes `<lang>-ref.wav` beside `ref.json` and records which Gemini voice
and model produced each clip.

3. Render one episode and listen before keeping it.

## The two things worth knowing

**This spends Gemini quota** (~30 s of audio per language) and it **changes how
every later episode sounds**. It is not a step to re-run casually — that is why
the confirmation flag has no default.

**`*-ref.wav` is gitignored.** A voice clone reference is close to biometric
data: anyone with the clip and its text can synthesize speech in that voice.
Keep the wav files out of any repository you publish, and treat them like a
credential if the voice is yours rather than a synthesized one.

## Format

`ref.json` needs one entry per show key in `config.toml`, each with a `text`
field. `make_voice_refs.py` fills in `source`, `model`, `voice` and
`duration_s` itself. Clips are s16le / 24 kHz / mono, the only format
vLLM-Omni reads.

About 30 seconds is enough — a longer reference did not measurably improve the
clone in testing, and a clip whose text drifts out of alignment hurts more than
extra length helps.
