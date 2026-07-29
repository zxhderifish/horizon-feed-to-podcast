import subprocess
from pathlib import Path

import pytest

import podcast_tts


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


def test_assemble_silent_boundary_adds_nothing():
    sfx = {"intro": b"II", "sting": b"SS", "quickhits": b"QQ", "outro": b"OO"}
    out = podcast_tts.assemble([b"a", b"b"], ["none"], sfx)
    assert out == b"II" + b"a" + b"b" + b"OO"


def test_assemble_inserts_sfx_in_order():
    sfx = {"intro": b"II", "sting": b"SS", "quickhits": b"QQ", "outro": b"OO"}
    out = podcast_tts.assemble([b"a", b"b", b"c"], ["sting", "quickhits"], sfx)
    assert out == b"II" + b"a" + b"SS" + b"b" + b"QQ" + b"c" + b"OO"


def test_assemble_without_sfx_is_plain_concat():
    assert podcast_tts.assemble([b"a", b"b"], ["sting"], {}) == b"ab"


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
