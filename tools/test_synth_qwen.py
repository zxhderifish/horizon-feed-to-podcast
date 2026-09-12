import json
import subprocess
import sys
from array import array
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "remote"))
import synth_qwen


def fake_synth(segments, lang):
    assert lang == "zh"
    return [f"<{s}>!".encode("utf-8") for s in segments], 24000


def test_run_returns_framed_pcm_in_order():
    payload = json.dumps({"lang": "zh", "segments": ["a", "b"]}).encode("utf-8")
    out = synth_qwen.run(payload, synth=fake_synth)
    head, body = out.split(b"\n", 1)
    parsed = json.loads(head)
    assert parsed["sr"] == 24000 and parsed["lens"] == [4, 4]
    assert parsed["report"]["takes"] == 1
    assert body == b"<a>!<b>!"


def test_run_rejects_empty_segments():
    payload = json.dumps({"lang": "zh", "segments": []}).encode("utf-8")
    with pytest.raises(ValueError, match="no segments"):
        synth_qwen.run(payload, synth=fake_synth)


def test_run_rejects_unknown_language():
    payload = json.dumps({"lang": "fr", "segments": ["a"]}).encode("utf-8")
    with pytest.raises(ValueError, match="unsupported language"):
        synth_qwen.run(payload, synth=fake_synth)


def test_order_request_ids_parses_the_vllm_omni_format():
    ids = ["2_c3d4e5f6-0000-4000-8000-000000000002",
           "0_a1b2c3d4-0000-4000-8000-000000000000",
           "1_b2c3d4e5-0000-4000-8000-000000000001"]
    assert synth_qwen.order_request_ids(ids, 3) == [ids[1], ids[2], ids[0]]


def test_order_request_ids_is_not_fooled_by_digits_in_the_uuid():
    # The old digit-scraping approach turned "0_9f8a1b2c" into 912 and aborted.
    ids = ["0_99999999-0000-4000-8000-000000000000",
           "1_00000000-0000-4000-8000-000000000000"]
    assert synth_qwen.order_request_ids(ids, 2) == ids


def test_order_request_ids_rejects_an_unexpected_format():
    with pytest.raises(RuntimeError, match="unexpected request id format"):
        synth_qwen.order_request_ids(["req-0", "req-1"], 2)


def test_order_request_ids_rejects_a_non_permutation():
    with pytest.raises(RuntimeError, match="cannot recover segment order"):
        synth_qwen.order_request_ids(["0_a", "2_b"], 2)


def test_order_request_ids_handles_a_single_segment():
    assert synth_qwen.order_request_ids(["0_abc"], 1) == ["0_abc"]


def test_reserve_stdout_protects_fd1_in_a_real_subprocess():
    # vLLM (and tqdm, and anything a forked child writes) targets fd 1 by
    # default -- prove that after _reserve_stdout(), neither a Python-level
    # print() nor a raw os.write(1, ...) can land ahead of the real payload.
    remote_dir = str(Path(synth_qwen.__file__).resolve().parent)
    driver = f"""
import os, sys
sys.path.insert(0, {remote_dir!r})
import synth_qwen
saved = synth_qwen._reserve_stdout()
print("noise from a library logger")
os.write(1, b"more noise via a raw fd write")
saved.write(b"PAYLOAD")
saved.flush()
"""
    result = subprocess.run([sys.executable, "-c", driver], capture_output=True, timeout=30)
    assert result.stdout == b"PAYLOAD"
    assert b"noise" in result.stderr


def test_run_handles_english():
    seen = {}

    def synth(segments, lang):
        seen["lang"] = lang
        return [b"EN"], 24000

    out = synth_qwen.run(json.dumps({"lang": "en", "segments": ["hi"]}).encode("utf-8"),
                         synth=synth)
    assert seen["lang"] == "en"
    head, body = out.split(b"\n", 1)
    assert json.loads(head)["lens"] == [2] and body == b"EN"


def _pcm(level: int, seconds: float, sr: int = 24000) -> bytes:
    count = int(seconds * sr)
    return array("h", [level, -level] * (count // 2)).tobytes()


def test_tokenize_and_asr_ratio_ignore_spacing_and_punctuation():
    assert synth_qwen.tokenize("API 16.0.4，模型 A。") == [
        "api", "16.0", "4", "模", "型", "a"
    ]
    assert synth_qwen.asr_ratio("同一个 API 授权", "同一个API授权") == 1.0
    assert synth_qwen.asr_ratio("同一个 API 授权", "同一个API索权") < 1.0


def test_frame_rms_max_finds_a_loud_half_second():
    quiet = _pcm(3277, 1.0)
    burst = _pcm(13107, 0.5)
    assert synth_qwen.frame_rms_max(quiet, 24000) == pytest.approx(0.10, abs=0.005)
    assert synth_qwen.frame_rms_max(quiet + burst, 24000) == pytest.approx(0.40, abs=0.005)
    assert synth_qwen.frame_rms_max(b"", 24000) == 0.0


def test_frame_rms_max_stays_python_311_compatible():
    assert "sumprod" not in synth_qwen.frame_rms_max.__code__.co_names


def test_pick_take_uses_ratio_after_disqualifying_loud_takes():
    assert synth_qwen.pick_take([0.90, 0.95], [0.20, 0.21]) == (1, False)
    assert synth_qwen.pick_take([0.80, 0.99], [0.20, 0.45]) == (0, False)
    assert synth_qwen.pick_take([0.99, 0.80], [0.45, 0.35]) == (1, True)
    assert synth_qwen.pick_take(None, [0.45, 0.20, 0.21]) == (1, False)


def test_run_draws_takes_segment_major_and_keeps_the_best():
    seen = {}

    def synth(texts, lang):
        seen["texts"] = texts
        return [_pcm(1000 + i, 0.1) for i in range(len(texts))], 24000

    def asr(pcms, sr, lang):
        return ["API 索权", "API 授权", "第二段", "第二段"]

    payload = json.dumps({
        "lang": "zh", "segments": ["API 授权", "第二段"], "takes": 2
    }).encode("utf-8")
    head, body = synth_qwen.run(payload, synth=synth, asr=asr).split(b"\n", 1)
    report = json.loads(head)["report"]
    assert seen["texts"] == ["API 授权", "API 授权", "第二段", "第二段"]
    assert [segment["pick"] for segment in report["segments"]] == [1, 0]
    assert report["asr"] == "large-v3"
    assert body == _pcm(1001, 0.1) + _pcm(1002, 0.1)


def test_run_degrades_to_loudness_only_and_validates_inputs():
    def synth(texts, lang):
        return [_pcm(13107, 0.1), _pcm(1000, 0.1)], 24000

    payload = json.dumps({"lang": "zh", "segments": ["x"], "takes": 2}).encode()
    head, body = synth_qwen.run(payload, synth=synth, asr=lambda *_: None).split(b"\n", 1)
    report = json.loads(head)["report"]
    assert report["asr"] == "off" and report["segments"][0]["pick"] == 1
    assert body == _pcm(1000, 0.1)

    with pytest.raises(ValueError, match="takes"):
        synth_qwen.run(json.dumps({"lang": "zh", "segments": ["x"], "takes": 0}).encode(),
                       synth=synth)
    with pytest.raises(RuntimeError, match="malformed s16le PCM"):
        synth_qwen.run(payload, synth=lambda *_: ([b"x", b"yy"], 24000))


def test_helper_loader_names_the_missing_file_and_the_doc(tmp_path):
    # end2end.py is the one remote dependency _sync_remote_files does not push
    with pytest.raises(RuntimeError) as ei:
        synth_qwen._load_prompt_len_estimator(str(tmp_path))
    msg = str(ei.value)
    assert "end2end.py" in msg and "local-tts-setup.md" in msg


def test_helper_loader_rejects_a_drifted_helper(tmp_path):
    (tmp_path / "end2end.py").write_text("def something_else():\n    pass\n")
    with pytest.raises(RuntimeError) as ei:
        synth_qwen._load_prompt_len_estimator(str(tmp_path))
    msg = str(ei.value)
    assert "_estimate_prompt_len" in msg and "local-tts-setup.md" in msg


def test_helper_loader_returns_the_estimator(tmp_path):
    (tmp_path / "end2end.py").write_text(
        "def _estimate_prompt_len(info, model, _cache={}):\n    return 7\n")
    fn = synth_qwen._load_prompt_len_estimator(str(tmp_path))
    assert fn({}, "m") == 7


# --- the whole synthesizer process, across the pipe, into the local parser

_FAKE_ENGINE = '''
import json, os, sys, types, runpy

HERE = os.path.dirname(os.path.abspath(__file__))
# This is the synced layout, where synth_qwen.py sits beside the assets and
# finds them from its own dirname. Drop any inherited override so the test
# cannot silently pass by reading the real repo instead.
os.environ.pop("TTS_ASSET_DIR", None)


class _Arr:
    """Just enough numpy for vllm_synth's float32 -> s16le conversion."""

    def __init__(self, tag):
        self.tag = tag

    def flatten(self):
        return self

    def __mul__(self, k):
        return self

    def astype(self, dtype):
        return self

    def tobytes(self):
        return self.tag


class _Audio:
    def __init__(self, tag):
        self.tag = tag

    def float(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return _Arr(self.tag)


class _Out:
    def __init__(self, rid, tag):
        self.request_id = rid
        self.outputs = [types.SimpleNamespace(
            multimodal_output={"audio": _Audio(tag), "sr": 24000})]


class Omni:
    def __init__(self, **kw):
        # vLLM 0.28 logs to fd 1 by default; this is the corruption that cost a
        # night on hardware. Both a Python print and a raw fd write, because a
        # forked engine-core child does the latter.
        print("INFO 09-03 08:52:21 [core.py:1] engine start")
        os.write(1, b"INFO raw fd-1 write from a worker\\n")

    def generate(self, inputs):
        langs = [i["additional_information"]["language"][0] for i in inputs]
        os.write(2, ("LANGS=" + ",".join(langs) + "\\n").encode("utf-8"))
        # real vllm-omni id format, returned out of submission order
        outs = [_Out("%d_9f8a1b2c-0000-4000-8000-%012d" % (i, i), b"seg%d" % i)
                for i in range(len(inputs))]
        return list(reversed(outs))

    def close(self):
        os.write(2, b"ENGINE_CLOSED\\n")


np = types.ModuleType("numpy")
np.clip = lambda a, lo, hi: a
np.asarray = lambda x: x
sys.modules["numpy"] = np
sys.modules["torch"] = types.ModuleType("torch")
vo = types.ModuleType("vllm_omni")
vo.Omni = Omni
sys.modules["vllm_omni"] = vo

runpy.run_path(os.path.join(HERE, "synth_qwen.py"), run_name="__main__")
'''

_FAKE_HELPER = "def _estimate_prompt_len(info, model, _cache={}):\n    return 8\n"


def _deploy(tmp_path, script_src: str) -> Path:
    """Mirror what _sync_remote_files lays out on the GPU box, plus the un-synced
    helper and a GPU-free engine."""
    (tmp_path / "synth_qwen.py").write_text(script_src, encoding="utf-8")
    voice = tmp_path / "assets" / "voice"
    voice.mkdir(parents=True)
    repo_voice = Path(synth_qwen.__file__).resolve().parent.parent.parent / "assets" / "voice"
    # The real clips are gitignored, so the shipped template stands in — this
    # test only reads each language's ref text, never the audio.
    (voice / "ref.json").write_bytes((repo_voice / "ref.example.json").read_bytes())
    (tmp_path / "vllm_omni_helpers").mkdir()
    (tmp_path / "vllm_omni_helpers" / "end2end.py").write_text(_FAKE_HELPER, encoding="utf-8")
    driver = tmp_path / "driver.py"
    driver.write_text(_FAKE_ENGINE, encoding="utf-8")
    return driver


@pytest.mark.parametrize("lang,expected", [("zh", "Chinese"), ("en", "English")])
def test_remote_main_stdout_parses_as_a_framed_payload(tmp_path, lang, expected):
    """Cross the process boundary the daily job actually crosses: run the real
    __main__ and feed its raw stdout straight into podcast_tts.unframe_pcms.
    A log line on fd 1 or a scrambled request id shows up here as a parse
    failure or wrong bytes -- both of the defects real hardware found."""
    import podcast_tts

    src = Path(synth_qwen.__file__).resolve().read_text(encoding="utf-8")
    driver = _deploy(tmp_path, src)
    payload = json.dumps({"lang": lang, "segments": ["a", "b", "c"]}).encode("utf-8")
    r = subprocess.run([sys.executable, str(driver)], input=payload,
                       capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-2000:]

    pcms, sr, report = podcast_tts.unframe_pcms(r.stdout)
    assert pcms == [b"seg0", b"seg1", b"seg2"], "submission order must be restored"
    assert sr == 24000
    assert report["takes"] == 1
    assert b"INFO" in r.stderr and b"INFO" not in r.stdout  # fd 1 stayed clean
    assert ("LANGS=" + ",".join([expected] * 3)).encode("utf-8") in r.stderr


def test_remote_main_best_of_two_without_whisper_installed(tmp_path):
    import podcast_tts

    src = Path(synth_qwen.__file__).resolve().read_text(encoding="utf-8")
    driver = _deploy(tmp_path, src)
    payload = json.dumps({"lang": "zh", "segments": ["a", "b"], "takes": 2}).encode()
    result = subprocess.run(
        [sys.executable, str(driver)], input=payload, capture_output=True, timeout=60
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")[-2000:]
    pcms, sr, report = podcast_tts.unframe_pcms(result.stdout)
    assert pcms == [b"seg0", b"seg2"] and sr == 24000
    assert report["takes"] == 2 and report["asr"] == "off"
    assert b"ENGINE_CLOSED" in result.stderr
