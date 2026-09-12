import json
import subprocess
import wave
from array import array
from pathlib import Path

import pytest

import make_voice_refs
import podcast_tts

import sys

sys.path.insert(0, str(Path(podcast_tts.__file__).resolve().parent / "remote"))
import synth_qwen  # noqa: E402


@pytest.fixture
def over_ssh(monkeypatch):
    """Put the model on another box. With TTS_REMOTE_HOST unset — the default,
    and the common case for a single-GPU-machine setup — the same code path is
    a plain subprocess; see the same-machine tests at the bottom."""
    monkeypatch.setattr(podcast_tts, "REMOTE_HOST", "gpu-box")
    return "gpu-box"


def test_split_script_on_hr_separators():
    md = "开场白第一段。\n\n---\n\n第二段内容。\n---\n第三段。"
    segs = podcast_tts.split_script(md)
    assert segs == ["开场白第一段。", "第二段内容。", "第三段。"]


def test_split_script_ignores_empty_segments():
    md = "---\n\nonly one real segment\n\n---\n\n"
    assert podcast_tts.split_script(md) == ["only one real segment"]


def test_parse_script_boundary_types():
    md = "open\n---\nitem one\n***\nquick hits\n---\nbye"
    segs, bounds = podcast_tts.parse_script(md)
    assert segs == ["open", "item one", "quick hits", "bye"]
    assert bounds == ["sting", "quickhits", "sting"]


def test_parse_script_ignores_leading_trailing_separators():
    segs, bounds = podcast_tts.parse_script("---\nonly\n***\n")
    assert segs == ["only"] and bounds == []


def test_parse_script_silent_boundary():
    md = "a\n***\nquick part 1\n~~~\nquick part 2 and sign-off"
    segs, bounds = podcast_tts.parse_script(md)
    assert segs == ["a", "quick part 1", "quick part 2 and sign-off"]
    assert bounds == ["quickhits", "none"]


def _tone(ms: int, amp: int = 20000) -> bytes:
    """Constant full-level PCM: a segment that ends while still sounding."""
    n = ms * podcast_tts.SAMPLE_RATE // 1000
    return array("h", [amp] * n).tobytes()


def _silence(ms: int) -> bytes:
    return b"\x00\x00" * (ms * podcast_tts.SAMPLE_RATE // 1000)


def _padded(pcm: bytes) -> bytes:
    """A segment that already satisfies both guarantees (quiet lead and
    quiet tail): assemble() must return it, and anything built from it,
    byte-for-byte."""
    return _silence(podcast_tts.MIN_LEAD_MS) + pcm + _silence(podcast_tts.MIN_TAIL_MS)


def _trailing_quiet_ms(pcm: bytes) -> float:
    n = 0
    for s in reversed(array("h", pcm)):
        if abs(s) >= 200:
            break
        n += 1
    return n * 1000 / podcast_tts.SAMPLE_RATE


def _leading_quiet_ms(pcm: bytes) -> float:
    n = 0
    for s in array("h", pcm):
        if abs(s) >= 200:
            break
        n += 1
    return n * 1000 / podcast_tts.SAMPLE_RATE


def _max_step(pcm: bytes) -> int:
    s = array("h", pcm)
    return max((abs(s[i + 1] - s[i]) for i in range(len(s) - 1)), default=0)


def test_assemble_silent_boundary_adds_nothing():
    sfx = {"intro": b"II", "sting": b"SS", "quickhits": b"QQ", "outro": b"OO"}
    a, b = _padded(b"aa"), _padded(b"bb")
    out = podcast_tts.assemble([a, b], ["none"], sfx)
    assert out == b"II" + a + b + b"OO"


def test_assemble_inserts_sfx_in_order():
    sfx = {"intro": b"II", "sting": b"SS", "quickhits": b"QQ", "outro": b"OO"}
    a, b, c = (_padded(x) for x in (b"aa", b"bb", b"cc"))
    out = podcast_tts.assemble([a, b, c], ["sting", "quickhits"], sfx)
    assert out == b"II" + a + b"SS" + b + b"QQ" + c + b"OO"


def test_assemble_without_sfx_concats_padded_segments():
    a, b = _padded(b"aa"), _padded(b"bb")
    assert podcast_tts.assemble([a, b], ["sting"], {}) == a + b


def test_assemble_pads_quiet_tail_before_sting():
    # Qwen3-TTS segments end mid-speech; the sting used to land on top of it.
    sting = array("h", [12345] * 1200).tobytes()
    out = podcast_tts.assemble([_tone(100), _tone(100)], ["sting"], {"sting": sting})
    cut = out.index(sting)
    assert _trailing_quiet_ms(out[:cut]) >= podcast_tts.MIN_TAIL_MS


def test_assemble_fades_instead_of_hard_cutting():
    # A pad without a fade only moves the step; prove the ramp is there.
    # Measure only up to the sting (i.e. just the padded first segment), not
    # the whole output: the sting's own shape and the second segment's
    # starting amplitude are unrelated to whether the cut was faded, and
    # folding them into one bound would make it pass or fail on their
    # account instead of the fade's.
    sting = array("h", [i * 100 for i in range(200)]).tobytes()
    out = podcast_tts.assemble([_tone(100), _tone(100)], ["sting"], {"sting": sting})
    cut = out.index(sting)
    assert _max_step(out[:cut]) < 500


def test_assemble_pads_quiet_lead_after_silence():
    # The owner's pop: local segments start at full amplitude straight out
    # of digital silence. A silent intro stands in for the real intro.pcm's
    # decay to silence.
    out = podcast_tts.assemble([_tone(100)], [], {"intro": _silence(50)})
    seg_start = len(_silence(50))
    assert _leading_quiet_ms(out[seg_start:]) >= podcast_tts.MIN_LEAD_MS


def test_assemble_fades_lead_instead_of_hard_cutting():
    # A pad without a fade only moves the step to the pad/speech boundary;
    # prove the ramp is there. A silent intro stands in for the real
    # intro.pcm's decay to silence — otherwise there is no prior sample to
    # step from at index 0.
    out = podcast_tts.assemble([_tone(100)], [], {"intro": _silence(50)})
    assert _max_step(out) < 500


def test_assemble_keeps_already_quiet_segment_byte_for_byte():
    seg = _silence(300) + _tone(100) + _silence(300)
    assert podcast_tts.assemble([seg], [], {}) == seg


def test_assemble_keeps_already_quiet_lead_unchanged():
    # Enough leading quiet already, but the tail still needs padding: the
    # lead bytes must come back untouched even though the segment as a
    # whole does not.
    seg = _silence(300) + _tone(100)
    out = podcast_tts.assemble([seg], [], {})
    assert out[: len(_silence(300))] == _silence(300)


def test_assemble_guarantees_tail_and_lead_at_every_join():
    # Every join kind a segment can follow: intro, a sting, quickhits, and
    # a bare `~~~` join (boundary "none").
    seg = _tone(100)
    sfx = {"intro": array("h", [6000] * 100).tobytes(),
           "sting": array("h", [9000] * 100).tobytes(),
           "quickhits": array("h", [8000] * 100).tobytes(),
           "outro": array("h", [7000] * 100).tobytes()}
    preceding = ("intro", "sting", "quickhits", "none")
    trailing = ("sting", "quickhits", "none", "outro")
    out = podcast_tts.assemble([seg] * 4, ["sting", "quickhits", "none"], sfx)
    lead_pad = len(_silence(podcast_tts.MIN_LEAD_MS))
    tail_pad = len(_silence(podcast_tts.MIN_TAIL_MS))
    pos = len(sfx["intro"])
    for before, after in zip(preceding, trailing):
        assert _leading_quiet_ms(out[pos:]) >= podcast_tts.MIN_LEAD_MS, before
        pos += lead_pad + len(seg) + tail_pad  # no quiet in `seg`: full pad both ends
        assert _trailing_quiet_ms(out[:pos]) >= podcast_tts.MIN_TAIL_MS, after
        pos += len(sfx.get(after, b""))
    assert pos == len(out)  # nothing dropped, duplicated or over-padded


def test_assemble_bare_join_gap_covers_both_guarantees():
    # At a bare join, segment 1's tail pad and segment 2's lead pad are
    # both true silence, so they merge into one contiguous quiet run of at
    # least MIN_TAIL_MS + MIN_LEAD_MS between the two segments' audible
    # content.
    out = podcast_tts.assemble([_tone(100), _tone(100)], ["none"], {})
    mid = len(out) // 2  # identical fixtures on both sides of the join
    gap = _trailing_quiet_ms(out[:mid]) + _leading_quiet_ms(out[mid:])
    assert gap >= podcast_tts.MIN_TAIL_MS + podcast_tts.MIN_LEAD_MS


def test_assemble_length_grows_by_exactly_the_padding():
    loud = _tone(100)  # no quiet either end: gets full lead + tail pad
    already_padded = _padded(_tone(100))  # already satisfies both: untouched
    out = podcast_tts.assemble([loud, already_padded], ["none"], {})
    extra = len(_silence(podcast_tts.MIN_LEAD_MS)) + len(_silence(podcast_tts.MIN_TAIL_MS))
    assert len(out) == len(loud) + extra + len(already_padded)


def test_assemble_trims_over_long_quiet_at_every_join():
    # The band's upper edge, at the same four join kinds the floor is
    # checked at. With the synthesized reference clip Qwen opens with up to
    # 652ms of its own quiet; everything past the cap is silence and goes.
    seg = _silence(600) + _tone(100) + _silence(600)
    sfx = {"intro": array("h", [6000] * 100).tobytes(),
           "sting": array("h", [9000] * 100).tobytes(),
           "quickhits": array("h", [8000] * 100).tobytes(),
           "outro": array("h", [7000] * 100).tobytes()}
    preceding = ("intro", "sting", "quickhits", "none")
    trailing = ("sting", "quickhits", "none", "outro")
    out = podcast_tts.assemble([seg] * 4, ["sting", "quickhits", "none"], sfx)
    capped = len(_silence(podcast_tts.MAX_QUIET_MS))
    pos = len(sfx["intro"])
    for before, after in zip(preceding, trailing):
        assert _leading_quiet_ms(out[pos:]) == podcast_tts.MAX_QUIET_MS, before
        pos += capped + len(_tone(100)) + capped
        assert _trailing_quiet_ms(out[:pos]) == podcast_tts.MAX_QUIET_MS, after
        pos += len(sfx.get(after, b""))
    assert pos == len(out)  # nothing dropped, duplicated or over-trimmed


def test_assemble_trim_removes_silence_only():
    # The cut is measured off the quiet run, so an off-by-one eats speech:
    # compare the whole segment byte-for-byte, silence padding included. No
    # fade either — both sides of the cut are already silent.
    body = _tone(100)
    quiet = _silence(podcast_tts.MAX_QUIET_MS)
    out = podcast_tts.assemble([_silence(600) + body + _silence(600)], [], {})
    assert out == quiet + body + quiet


def test_assemble_keeps_quiet_at_the_band_edges_unchanged():
    # MAX one end, MIN the other: both inside the band, so passthrough —
    # the same guarantee already-quiet segments have today.
    seg = (_silence(podcast_tts.MAX_QUIET_MS) + _tone(100)
           + _silence(podcast_tts.MIN_TAIL_MS))
    assert podcast_tts.assemble([seg], [], {}) == seg


def test_assemble_does_not_fade_a_segment_it_did_not_pad():
    # The fade exists to hide a pad's step, so no pad means no fade. Every
    # other passthrough fixture here is digital silence, which a fade cannot
    # alter — an unconditional fade would sail through them. Quiet at
    # amplitude 100 is below QUIET_LEVEL (so still "quiet", still in band)
    # but non-zero, so a ramp over it shows up in the bytes.
    hum = _tone(300, amp=100)
    seg = hum + _tone(100) + hum
    assert podcast_tts.assemble([seg], [], {}) == seg


def test_assemble_pads_one_edge_and_trims_the_other():
    seg = _silence(600) + _tone(100)  # lead past the cap, no tail at all
    out = podcast_tts.assemble([seg], [], {})
    assert _leading_quiet_ms(out) == podcast_tts.MAX_QUIET_MS
    assert _trailing_quiet_ms(out) >= podcast_tts.MIN_TAIL_MS
    assert len(out) == (len(_silence(podcast_tts.MAX_QUIET_MS)) + len(_tone(100))
                        + len(_silence(podcast_tts.MIN_TAIL_MS)))


def test_assemble_length_changes_by_padding_minus_trimming():
    loud = _tone(100)  # no quiet either end: full lead + tail pad
    slack = _silence(600) + _tone(100) + _silence(600)  # over the cap both ends
    out = podcast_tts.assemble([loud, slack], ["none"], {})
    pad = len(_silence(podcast_tts.MIN_LEAD_MS)) + len(_silence(podcast_tts.MIN_TAIL_MS))
    trim = 2 * len(_silence(600 - podcast_tts.MAX_QUIET_MS))
    assert len(out) == len(loud) + pad + len(slack) - trim


def test_load_sfx_reads_whatever_is_present(tmp_path):
    (tmp_path / "intro.pcm").write_bytes(b"\x00\x01")
    (tmp_path / "outro.pcm").write_bytes(b"\x02\x03")
    sfx = podcast_tts.load_sfx(tmp_path)
    assert set(sfx) == {"intro", "outro"}  # sting/quickhits simply absent


def test_load_sfx_missing_dir_is_empty(tmp_path):
    # The sound package is optional: no assets means speech-only episodes.
    assert podcast_tts.load_sfx(tmp_path / "nope") == {}


def test_pcm_to_mp3_roundtrip(tmp_path):
    # 1 second of silence, 24kHz s16le mono = 48000 bytes
    pcm = b"\x00\x00" * 24000
    out = tmp_path / "out.mp3"
    podcast_tts.pcm_to_mp3(pcm, out)
    assert out.exists() and out.stat().st_size > 0
    dur = podcast_tts.mp3_duration_s(out)
    assert 0.8 <= dur <= 1.3


def test_mp3_duration_error_surfaces_stderr(tmp_path):
    bad = tmp_path / "not_audio.mp3"
    bad.write_bytes(b"hello")
    with pytest.raises(RuntimeError) as ei:
        podcast_tts.mp3_duration_s(bad)
    msg = str(ei.value)
    assert "ffprobe" in msg
    # message must carry actual stderr content, not just the tool name/exit code
    prefix, _, tail = msg.partition("):")
    assert tail.strip()


def test_synthesize_raises_on_empty_script(tmp_path):
    # must raise before any genai client is constructed (no GEMINI_API_KEY set)
    script = tmp_path / "empty.md"
    script.write_text("---\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no segments"):
        podcast_tts.synthesize(script, tmp_path / "out.mp3", "zh")


def test_synth_all_gemini_retries_once_then_succeeds(monkeypatch):
    calls = []

    def flaky(client, text, voice):
        calls.append(text)
        if len(calls) == 1:
            raise RuntimeError("transient")
        return b"PCM"

    monkeypatch.setattr(podcast_tts, "synth_segment", flaky)
    monkeypatch.setattr(podcast_tts.time, "sleep", lambda *_: None)
    monkeypatch.setattr(podcast_tts, "_gemini_client", lambda: object())
    assert podcast_tts._synth_all_gemini(["a"], "zh") == [b"PCM"]
    assert len(calls) == 2


def test_synth_all_gemini_raises_after_two_failures(monkeypatch):
    monkeypatch.setattr(podcast_tts, "synth_segment",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(podcast_tts.time, "sleep", lambda *_: None)
    monkeypatch.setattr(podcast_tts, "_gemini_client", lambda: object())
    with pytest.raises(RuntimeError, match="segment 0 failed twice"):
        podcast_tts._synth_all_gemini(["a"], "zh")


def test_ref_example_covers_every_configured_show():
    """The clips themselves are gitignored — a voice clone reference is close
    to biometric data. What ships is the template, and it is only useful if it
    has an entry per show."""
    example = podcast_tts.VOICE_DIR / "ref.example.json"
    meta = json.loads(example.read_text(encoding="utf-8"))
    assert set(meta) == set(podcast_tts.shows())
    for lang, m in meta.items():
        assert m["text"].strip(), f"{lang} ref text must not be empty"


def test_no_voice_clip_is_committed():
    """Publishing one hands anyone who clones the repo the ability to
    synthesize speech in that voice."""
    assert not list(podcast_tts.VOICE_DIR.glob("*.wav"))


def test_make_voice_refs_writes_clip_and_updates_metadata(tmp_path, monkeypatch):
    """The wiring, without spending quota: whatever synth_segment returns must
    land in <lang>-ref.wav, and ref.json must end up describing those bytes."""
    (tmp_path / "ref.json").write_text(
        json.dumps({"zh": {"text": "参考文本"}}), encoding="utf-8")
    pcm = b"\x00\x10" * podcast_tts.SAMPLE_RATE  # exactly one second
    monkeypatch.setattr(podcast_tts, "synth_segment", lambda c, text, voice: pcm)

    meta = make_voice_refs.regenerate(["zh"], client=object(), voice_dir=tmp_path)

    assert meta["zh"]["duration_s"] == pytest.approx(1.0)
    assert meta["zh"]["voice"] == podcast_tts.voice_for("zh")
    assert meta["zh"]["text"] == "参考文本", "the text must survive verbatim"
    with wave.open(str(tmp_path / "zh-ref.wav")) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (
            1, 2, podcast_tts.SAMPLE_RATE), "vLLM-Omni reads only s16le mono"
        assert w.readframes(w.getnframes()) == pcm
    # and the file on disk, not just the return value, carries the metadata
    assert json.loads((tmp_path / "ref.json").read_text(encoding="utf-8")) == meta


def test_wire_roundtrip():
    pcms = [b"\x01\x02", b"", b"\xff" * 5]
    blob = synth_qwen.frame_pcms(pcms, 24000)
    got, sr, report = podcast_tts.unframe_pcms(blob)
    assert got == pcms
    assert sr == 24000
    assert report == {}


def test_wire_roundtrip_single_segment():
    pcms = [b"\x00" * 100]
    got, sr, report = podcast_tts.unframe_pcms(synth_qwen.frame_pcms(pcms, 24000))
    assert got == pcms and sr == 24000 and report == {}


def test_unframe_returns_and_validates_the_judge_report():
    report = {"takes": 2, "asr": "off", "segments": []}
    assert podcast_tts.unframe_pcms(
        synth_qwen.frame_pcms([b"AA"], 24000, report)
    ) == ([b"AA"], 24000, report)
    bad = json.dumps({"sr": 24000, "lens": [2], "report": []}).encode() + b"\nAA"
    with pytest.raises(ValueError, match="bad report"):
        podcast_tts.unframe_pcms(bad)


def test_unframe_rejects_truncated_payload():
    blob = synth_qwen.frame_pcms([b"\x01\x02\x03"], 24000)
    with pytest.raises(ValueError, match="truncated"):
        podcast_tts.unframe_pcms(blob[:-1])


def test_unframe_rejects_a_non_json_header():
    # what a vLLM log line landing on fd 1 ahead of the payload looks like
    blob = b"INFO 09-03 08:52:21 [patch.py:252] NVFP4 W4A4 weight_scale NaN-clamp: installed.\n" + b"\x01\x02\x03"
    with pytest.raises(ValueError, match="malformed TTS payload") as exc:
        podcast_tts.unframe_pcms(blob)
    assert "NVFP4" in str(exc.value)  # operator needs to see what actually arrived


def test_unframe_rejects_header_missing_lens():
    header = json.dumps({"sr": 24000}).encode("utf-8")
    with pytest.raises(ValueError, match="malformed TTS payload"):
        podcast_tts.unframe_pcms(header + b"\n" + b"\x01\x02")


def test_unframe_rejects_header_missing_sr():
    header = json.dumps({"lens": [2]}).encode("utf-8")
    with pytest.raises(ValueError, match="malformed TTS payload"):
        podcast_tts.unframe_pcms(header + b"\n" + b"\x01\x02")


def test_unframe_rejects_a_non_list_lens():
    header = json.dumps({"sr": 24000, "lens": 2}).encode("utf-8")
    with pytest.raises(ValueError, match="malformed TTS payload"):
        podcast_tts.unframe_pcms(header + b"\n" + b"\x01\x02")


def test_unframe_rejects_non_integer_lens_entries():
    header = json.dumps({"sr": 24000, "lens": ["2"]}).encode("utf-8")
    with pytest.raises(ValueError, match="malformed TTS payload"):
        podcast_tts.unframe_pcms(header + b"\n" + b"\x01\x02")


def test_synth_all_local_parses_remote_payload(monkeypatch):
    seen = {}

    def fake_run_synth(payload, timeout_s):
        seen["req"] = json.loads(payload.decode("utf-8"))
        seen["timeout"] = timeout_s
        return synth_qwen.frame_pcms([b"AA", b"BB"], 24000)

    monkeypatch.setattr(podcast_tts, "_run_synth", fake_run_synth)
    monkeypatch.setattr(podcast_tts, "_sync_remote_files", lambda deadline: None)
    monkeypatch.setattr(podcast_tts, "TTS_TAKES", 2)
    assert podcast_tts._synth_all_local(["a", "b"], "zh", 600) == ([b"AA", b"BB"], {})
    assert seen["req"] == {"lang": "zh", "segments": ["a", "b"], "takes": 2}
    assert seen["timeout"] == pytest.approx(600, abs=1)


def test_synth_all_local_rejects_wrong_sample_rate(monkeypatch):
    monkeypatch.setattr(podcast_tts, "_run_synth",
                        lambda p, t: synth_qwen.frame_pcms([b"AA"], 48000))
    monkeypatch.setattr(podcast_tts, "_sync_remote_files", lambda deadline: None)
    with pytest.raises(RuntimeError, match="sample rate"):
        podcast_tts._synth_all_local(["a"], "zh", 600)


def test_synth_all_local_rejects_segment_count_mismatch(monkeypatch):
    monkeypatch.setattr(podcast_tts, "_run_synth",
                        lambda p, t: synth_qwen.frame_pcms([b"AA"], 24000))
    monkeypatch.setattr(podcast_tts, "_sync_remote_files", lambda deadline: None)
    with pytest.raises(RuntimeError, match="expected 2 segments"):
        podcast_tts._synth_all_local(["a", "b"], "zh", 600)


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(podcast_tts.time, "sleep", lambda *_: None)


def test_synth_all_uses_local_and_never_calls_gemini(monkeypatch, no_sleep):
    monkeypatch.setattr(podcast_tts, "_synth_all_local", lambda s, l, t: ([b"L"], {}))
    monkeypatch.setattr(podcast_tts, "_synth_all_gemini",
                        lambda s, l: pytest.fail("Gemini must not be called"))
    pcms, path = podcast_tts.synth_all(["a"], "zh")
    assert pcms == [b"L"] and path == "local"


def test_judge_summary_covers_swaps_loudness_and_asr_degradation():
    report = {"takes": 2, "asr": "large-v3", "segments": [
        {"pick": 0, "loud": False}, {"pick": 1, "loud": True}
    ]}
    assert podcast_tts.judge_summary(report) == " (best-of-2: 1/2 swapped, 1 loud)"
    report["asr"] = "off"
    assert podcast_tts.judge_summary(report) == " (best-of-2: asr=off, 1 loud)"
    assert podcast_tts.judge_summary({}) == ""


def test_synth_all_marker_carries_the_judge_summary(monkeypatch, no_sleep):
    report = {"takes": 2, "asr": "large-v3", "segments": [
        {"pick": 1, "loud": False}
    ]}
    monkeypatch.setattr(
        podcast_tts, "_synth_all_local", lambda s, l, t: ([b"L"], report)
    )
    monkeypatch.setattr(
        podcast_tts, "_synth_all_gemini", lambda *_: pytest.fail("no fallback")
    )
    assert podcast_tts.synth_all(["a"], "zh") == (
        [b"L"], "local (best-of-2: 1/1 swapped, 0 loud)"
    )


def test_public_defaults_to_one_take_and_uses_wider_budgets():
    assert podcast_tts.TTS_TAKES == 1
    assert podcast_tts.ATTEMPT_TIMEOUT_S == 900
    assert podcast_tts.LOCAL_BUDGET_S == 1000


def test_synth_all_retries_three_times_then_falls_back(monkeypatch, no_sleep):
    attempts = []

    def failing(segments, lang, timeout_s):
        attempts.append(1)
        raise RuntimeError("nope")

    monkeypatch.setattr(podcast_tts, "_synth_all_local", failing)
    monkeypatch.setattr(podcast_tts, "_synth_all_gemini", lambda s, l: [b"G"])
    pcms, path = podcast_tts.synth_all(["a"], "zh")
    assert pcms == [b"G"]
    assert len(attempts) == 3
    assert path.startswith("gemini") and "nope" in path


def test_synth_all_treats_timeout_as_failure(monkeypatch, no_sleep):
    def timing_out(segments, lang, timeout_s):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=timeout_s)

    monkeypatch.setattr(podcast_tts, "_synth_all_local", timing_out)
    monkeypatch.setattr(podcast_tts, "_synth_all_gemini", lambda s, l: [b"G"])
    pcms, path = podcast_tts.synth_all(["a"], "zh")
    assert pcms == [b"G"] and "timeout" in path.lower()


def test_synth_all_stops_early_when_budget_is_spent(monkeypatch):
    attempts = []
    clock = {"t": 0.0}
    monkeypatch.setattr(podcast_tts.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(podcast_tts.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))

    def slow_failing(segments, lang, timeout_s):
        attempts.append(1)
        clock["t"] += podcast_tts.LOCAL_BUDGET_S  # one attempt eats the budget
        raise RuntimeError("slow")

    monkeypatch.setattr(podcast_tts, "_synth_all_local", slow_failing)
    monkeypatch.setattr(podcast_tts, "_synth_all_gemini", lambda s, l: [b"G"])
    pcms, path = podcast_tts.synth_all(["a"], "zh")
    assert pcms == [b"G"]
    assert len(attempts) == 1, "budget must stop the remaining retries"


def test_synth_all_local_disabled_by_env(monkeypatch, no_sleep):
    monkeypatch.setenv("TTS_LOCAL", "0")
    monkeypatch.setattr(podcast_tts, "_synth_all_local",
                        lambda *a: pytest.fail("local must be skipped"))
    monkeypatch.setattr(podcast_tts, "_synth_all_gemini", lambda s, l: [b"G"])
    pcms, path = podcast_tts.synth_all(["a"], "zh")
    assert pcms == [b"G"] and path == "gemini (disabled)"


def test_synthesize_reports_which_path_ran(tmp_path, monkeypatch):
    script = tmp_path / "s.md"
    script.write_text("hello\n\n---\n\nworld\n", encoding="utf-8")
    monkeypatch.setattr(podcast_tts, "synth_all", lambda s, l: ([b"\x00" * 4800] * 2, "local"))
    monkeypatch.setattr(podcast_tts, "load_sfx", lambda: {})
    dur, path_used = podcast_tts.synthesize(script, tmp_path / "o.mp3", "zh")
    assert path_used == "local"
    assert dur > 0


@pytest.fixture
def fake_clock(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(podcast_tts.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(podcast_tts.time, "sleep",
                        lambda s: clock.__setitem__("t", clock["t"] + s))
    return clock


def test_local_path_bounds_every_remote_call_and_the_whole_budget(monkeypatch, fake_clock, over_ssh):
    """A wedged-but-reachable GPU box: ssh/scp connect, then never answer.
    The sync is where that stalls first, so its cost must come out of the same
    budget — patching _synth_all_local away can never observe this.
    """
    STALL_S = 400.0
    calls = []

    def wedged(cmd, **kw):
        timeout = kw.get("timeout")
        calls.append((cmd[0], timeout))
        fake_clock["t"] += STALL_S if timeout is None else min(timeout, STALL_S)
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout or STALL_S)

    monkeypatch.setattr(podcast_tts.subprocess, "run", wedged)
    monkeypatch.setattr(podcast_tts, "_synth_all_gemini", lambda s, l: [b"G"])
    pcms, path = podcast_tts.synth_all(["a"], "zh")
    assert pcms == [b"G"]
    assert calls, "the local path must actually shell out"
    assert all(t is not None for _, t in calls), f"unbounded ssh/scp call: {calls}"
    assert fake_clock["t"] <= podcast_tts.LOCAL_BUDGET_S, (
        f"local path burned {fake_clock['t']}s of the "
        f"{podcast_tts.LOCAL_BUDGET_S}s budget"
    )
    assert "timeout" in path


def test_sync_bounds_and_keepalives_every_remote_call(monkeypatch, fake_clock, over_ssh):
    calls = []

    def record(cmd, **kw):
        calls.append((cmd, kw.get("timeout")))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(podcast_tts.subprocess, "run", record)
    podcast_tts._sync_remote_files(fake_clock["t"] + 120.0)
    assert len(calls) == 3
    for cmd, timeout in calls:
        joined = " ".join(cmd)
        assert "ServerAliveInterval=" in joined and "ServerAliveCountMax=" in joined, cmd
        assert timeout is not None and 0 < timeout <= 120.0, cmd


def test_sync_raises_timeout_expired_once_the_deadline_has_passed(fake_clock, over_ssh):
    with pytest.raises(subprocess.TimeoutExpired):
        podcast_tts._sync_remote_files(fake_clock["t"] - 1.0)


def test_run_synth_over_ssh_keepalives_and_honours_its_timeout(monkeypatch, over_ssh):
    seen = {}

    def record(cmd, **kw):
        seen.update(cmd=cmd, timeout=kw.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0, b"OUT", b"")

    monkeypatch.setattr(podcast_tts.subprocess, "run", record)
    assert podcast_tts._run_synth(b"{}", 42) == b"OUT"
    assert seen["timeout"] == 42
    assert "ServerAliveInterval=" in " ".join(seen["cmd"])


def test_synth_all_local_shares_one_deadline_between_sync_and_synth(monkeypatch, fake_clock, over_ssh):
    seen = {}

    def slow_sync(deadline):
        fake_clock["t"] += 100.0

    def fake_run_synth(payload, timeout_s):
        seen["timeout"] = timeout_s
        return synth_qwen.frame_pcms([b"AA"], 24000)

    monkeypatch.setattr(podcast_tts, "_sync_remote_files", slow_sync)
    monkeypatch.setattr(podcast_tts, "_run_synth", fake_run_synth)
    assert podcast_tts._synth_all_local(["a"], "zh", 600) == ([b"AA"], {})
    assert seen["timeout"] == pytest.approx(500.0), "sync time must come out of the attempt"


def test_synth_all_does_not_retry_after_a_timeout(monkeypatch, no_sleep):
    attempts = []

    def timing_out(segments, lang, timeout_s):
        attempts.append(timeout_s)
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=timeout_s)

    monkeypatch.setattr(podcast_tts, "_synth_all_local", timing_out)
    monkeypatch.setattr(podcast_tts, "_synth_all_gemini", lambda s, l: [b"G"])
    pcms, path = podcast_tts.synth_all(["a"], "zh")
    assert pcms == [b"G"]
    assert len(attempts) == 1, "a hung remote still holds VRAM; a retry gets OOM-killed"
    assert f"{podcast_tts.ATTEMPT_TIMEOUT_S}s" in path, "operator needs the granted duration"


def test_tts_marker_stays_one_short_line(monkeypatch, no_sleep):
    noisy = "remote TTS failed (exit 1): Traceback\n  File x\n\t" + "spew " * 200

    def failing(segments, lang, timeout_s):
        raise RuntimeError(noisy)

    monkeypatch.setattr(podcast_tts, "_synth_all_local", failing)
    monkeypatch.setattr(podcast_tts, "_synth_all_gemini", lambda s, l: [b"G"])
    _, path = podcast_tts.synth_all(["a"], "zh")
    assert "\n" not in path and "\t" not in path
    assert len(path) <= 220, f"tts= marker is {len(path)} chars"
    assert path.startswith("gemini (local failed:") and "remote TTS failed" in path


def test_languages_are_independent_zh_local_en_gemini(monkeypatch, no_sleep):
    """The CLI runs one process per language; zh succeeding locally must not
    carry en, and en falling back must not drag zh onto Gemini."""

    def local(segments, lang, timeout_s):
        if lang == "en":
            raise RuntimeError("en ref clip missing on the box")
        return [b"ZH"], {}

    monkeypatch.setattr(podcast_tts, "_synth_all_local", local)
    monkeypatch.setattr(podcast_tts, "_synth_all_gemini",
                        lambda s, l: [f"G-{l}".encode("utf-8")])
    assert podcast_tts.synth_all(["a"], "zh") == ([b"ZH"], "local")
    pcms, path = podcast_tts.synth_all(["a"], "en")
    assert pcms == [b"G-en"]
    assert path.startswith("gemini") and "en ref clip" in path


def test_synth_all_local_sends_english_through_to_the_remote(monkeypatch):
    seen = {}

    def fake_run_synth(payload, timeout_s):
        seen["req"] = json.loads(payload.decode("utf-8"))
        return synth_qwen.frame_pcms([b"EN"], 24000)

    monkeypatch.setattr(podcast_tts, "_run_synth", fake_run_synth)
    monkeypatch.setattr(podcast_tts, "_sync_remote_files", lambda deadline: None)
    assert podcast_tts._synth_all_local(["hello"], "en", 600) == ([b"EN"], {})
    assert seen["req"] == {"lang": "en", "segments": ["hello"], "takes": 1}


def test_gemini_uses_a_distinct_voice_per_language(monkeypatch):
    voices = []
    monkeypatch.setattr(podcast_tts, "synth_segment",
                        lambda client, text, voice: voices.append(voice) or b"P")
    monkeypatch.setattr(podcast_tts.time, "sleep", lambda *_: None)
    monkeypatch.setattr(podcast_tts, "_gemini_client", lambda: object())
    for lang in ("zh", "en"):
        assert podcast_tts._synth_all_gemini(["a"], lang) == [b"P"]
    assert voices == [podcast_tts.voice_for("zh"), podcast_tts.voice_for("en")]
    assert voices[0] != voices[1]


# --- same machine: TTS_REMOTE_HOST unset, no SSH in the picture -------------


def test_same_machine_runs_a_subprocess_instead_of_ssh(monkeypatch):
    monkeypatch.setattr(podcast_tts, "REMOTE_HOST", "")
    argv, env = podcast_tts._tts_command()
    assert argv == [podcast_tts.LOCAL_PYTHON, str(podcast_tts.LOCAL_SCRIPT)]
    assert "ssh" not in argv[0]
    # synth_qwen.py lives in tools/remote/ but the assets sit at the repo root,
    # so it cannot find them from its own dirname the way the synced copy does.
    assert env["TTS_ASSET_DIR"] == str(podcast_tts.REPO_ROOT)


def test_over_ssh_runs_ssh_with_keepalives(over_ssh):
    argv, env = podcast_tts._tts_command()
    assert argv[0] == "ssh" and over_ssh in argv
    assert "ServerAliveInterval=" in " ".join(argv)
    assert env is None, "the remote reads its assets from the synced directory"


def test_same_machine_skips_the_sync_step(monkeypatch):
    """There is nothing to copy when the files are already here, and an scp to
    localhost would need an sshd the user never set up."""
    monkeypatch.setattr(podcast_tts, "REMOTE_HOST", "")
    monkeypatch.setattr(podcast_tts, "_sync_remote_files",
                        lambda deadline: pytest.fail("nothing to sync locally"))
    monkeypatch.setattr(podcast_tts, "_run_synth",
                        lambda p, t: synth_qwen.frame_pcms([b"AA"], 24000))
    assert podcast_tts._synth_all_local(["a"], "zh", 600) == ([b"AA"], {})


def test_synth_qwen_reads_assets_from_its_own_dir_by_default(monkeypatch):
    """Over SSH the file is copied next to assets/voice/, so its dirname is
    the answer and TTS_ASSET_DIR is never set."""
    monkeypatch.delenv("TTS_ASSET_DIR", raising=False)
    assert synth_qwen.base_dir() == str(Path(synth_qwen.__file__).resolve().parent)


def test_synth_qwen_honours_tts_asset_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_ASSET_DIR", str(tmp_path))
    assert synth_qwen.base_dir() == str(tmp_path)


def test_run_synth_surfaces_the_synthesizer_stderr(monkeypatch):
    """A crashed local model is the failure the operator will actually hit;
    the reason has to survive into the tts= marker."""
    def failed(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, b"", b"CUDA out of memory")

    monkeypatch.setattr(podcast_tts.subprocess, "run", failed)
    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        podcast_tts._run_synth(b"{}", 42)
