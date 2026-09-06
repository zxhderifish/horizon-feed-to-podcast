import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "remote"))
import synth_qwen


def fake_synth(segments, lang):
    assert lang == "zh"
    return [f"<{s}>".encode("utf-8") for s in segments], 24000


def test_run_returns_framed_pcm_in_order():
    payload = json.dumps({"lang": "zh", "segments": ["a", "b"]}).encode("utf-8")
    out = synth_qwen.run(payload, synth=fake_synth)
    head, body = out.split(b"\n", 1)
    assert json.loads(head) == {"sr": 24000, "lens": [3, 3]}
    assert body == b"<a><b>"


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
    assert out.split(b"\n", 1) == [b'{"sr": 24000, "lens": [2]}', b"EN"]


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

    pcms, sr = podcast_tts.unframe_pcms(r.stdout)
    assert pcms == [b"seg0", b"seg1", b"seg2"], "submission order must be restored"
    assert sr == 24000
    assert b"INFO" in r.stderr and b"INFO" not in r.stdout  # fd 1 stayed clean
    assert ("LANGS=" + ",".join([expected] * 3)).encode("utf-8") in r.stderr
