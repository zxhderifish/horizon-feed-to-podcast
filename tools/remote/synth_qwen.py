"""Stateless Qwen3-TTS synthesizer, run on the GPU box — locally or over SSH.

stdin : {"lang": "zh", "segments": ["...", ...]}
stdout: {"sr": 24000, "lens": [n1, ...]}\n  followed by the concatenated PCM
stderr: everything else. vLLM's own logger (and tqdm, and anything a forked
        worker writes) defaults to fd 1, so __main__ reserves the real fd 1
        via _reserve_stdout() before vLLM is ever imported, and redirects
        fd 1 to fd 2 for the rest of the process.

vLLM is imported inside the function so this module can be imported — and
tested — on a machine with no GPU.
"""

import difflib
import json
import math
import os
import re
from array import array
from typing import List, Optional, Tuple

MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
SAMPLE_RATE = 24000
# The one remote file podcast_tts.py does NOT sync: it is copied out of the
# pinned vllm-omni checkout during setup, so it can drift or vanish.
HELPER_DOC = "docs/local-tts-setup.md (Step 1)"


def frame_pcms(pcms: List[bytes], sr: int, report: Optional[dict] = None) -> bytes:
    header = {"sr": sr, "lens": [len(p) for p in pcms]}
    if report is not None:
        header["report"] = report
    return json.dumps(header).encode("utf-8") + b"\n" + b"".join(pcms)


LANGS = {"zh": "Chinese", "en": "English"}
LOUD_RMS_MAX = 0.30
FRAME_S = 0.5
ASR_MODEL = "large-v3"
_TOK = re.compile(r"[A-Za-z][A-Za-z0-9.\-]*|[0-9]+(?:\.[0-9]+)?|[一-鿿]")


def tokenize(text: str) -> List[str]:
    """Drop punctuation and spacing while preserving words, numbers and CJK."""
    return [m.group().lower().rstrip(".") for m in _TOK.finditer(text)]


def asr_ratio(script: str, transcript: str) -> float:
    """Token similarity between the requested script and one ASR transcript."""
    return difflib.SequenceMatcher(
        None, tokenize(script), tokenize(transcript), autojunk=False
    ).ratio()


def frame_rms_max(pcm: bytes, sr: int) -> float:
    """Return the loudest half-second RMS in s16le mono PCM, full scale 1.0."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if not samples:
        return 0.0
    win = max(1, int(sr * FRAME_S))
    worst = 0.0
    for start in range(0, len(samples), win):
        frame = samples[start:start + win]
        if len(frame) < win and start > 0:
            break
        # Keep the public package compatible with its documented Python 3.11+.
        mean_square = sum(sample * sample for sample in frame) / len(frame)
        worst = max(worst, math.sqrt(mean_square))
    return worst / 32768.0


def pick_take(
    ratios: Optional[List[float]], rms_maxes: List[float]
) -> Tuple[int, bool]:
    """Pick the best quiet take, or the least-loud take if every one is loud."""
    quiet = [i for i, rms in enumerate(rms_maxes) if rms <= LOUD_RMS_MAX]
    if not quiet:
        return min(range(len(rms_maxes)), key=lambda i: rms_maxes[i]), True
    if ratios is None:
        return quiet[0], False
    return max(quiet, key=lambda i: (ratios[i], -i)), False


def transcribe(pcms: List[bytes], sr: int, lang: str) -> Optional[List[str]]:
    """Transcribe takes on CUDA, or return None for loudness-only judging."""
    import sys
    import tempfile
    import wave

    try:
        # Some CUDA wheels keep their shared libraries inside Python packages.
        # Preload them globally before ctranslate2 imports its CUDA backend.
        try:
            import ctypes
            import glob
            import nvidia.cublas.lib
            import nvidia.cudnn.lib

            for directory in (
                nvidia.cublas.lib.__path__[0],
                nvidia.cudnn.lib.__path__[0],
            ):
                for library in sorted(glob.glob(os.path.join(directory, "*.so*"))):
                    ctypes.CDLL(library, mode=ctypes.RTLD_GLOBAL)
        except Exception:
            pass

        from faster_whisper import WhisperModel

        model = WhisperModel(ASR_MODEL, device="cuda", compute_type="float16")
        transcripts = []
        with tempfile.TemporaryDirectory() as tmp:
            for i, pcm in enumerate(pcms):
                path = os.path.join(tmp, f"{i}.wav")
                with wave.open(path, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(sr)
                    wav.writeframes(pcm)
                segments, _ = model.transcribe(path, language=lang, beam_size=5)
                transcripts.append("".join(segment.text for segment in segments))
        return transcripts
    except Exception as error:
        print(
            f"[judge] faster_whisper unavailable: {error!r}; "
            "judging by loudness only",
            file=sys.stderr,
        )
        return None


def run(payload: bytes, synth=None, asr=None) -> bytes:
    """Draw N takes per segment, judge them, and return only each winner."""
    req = json.loads(payload.decode("utf-8"))
    lang = req["lang"]
    if lang not in LANGS:
        raise ValueError(f"unsupported language: {lang}")
    segments = req["segments"]
    if not segments:
        raise ValueError("no segments in request")
    takes = req.get("takes", 1)
    if not isinstance(takes, int) or isinstance(takes, bool) or takes < 1:
        raise ValueError(f"takes must be a positive integer, got {takes!r}")

    texts = [segment for segment in segments for _ in range(takes)]
    pcms, sr = (synth or vllm_synth)(texts, lang)
    if len(pcms) != len(texts):
        raise RuntimeError(f"expected {len(texts)} takes, got {len(pcms)}")
    if any(len(pcm) % 2 for pcm in pcms):
        raise RuntimeError("synth returned malformed s16le PCM (odd byte length)")

    transcripts = (asr or transcribe)(pcms, sr, lang) if takes > 1 else None
    if transcripts is not None and len(transcripts) != len(pcms):
        raise RuntimeError(f"expected {len(pcms)} transcripts, got {len(transcripts)}")

    chosen = []
    segment_reports = []
    for i, script in enumerate(segments):
        group = pcms[i * takes:(i + 1) * takes]
        rms_maxes = [frame_rms_max(pcm, sr) for pcm in group]
        ratios = None
        if transcripts is not None:
            ratios = [
                asr_ratio(script, transcript)
                for transcript in transcripts[i * takes:(i + 1) * takes]
            ]
        pick, loud = pick_take(ratios, rms_maxes)
        chosen.append(group[pick])
        segment_reports.append({
            "pick": pick,
            "ratios": None if ratios is None else [round(ratio, 4) for ratio in ratios],
            "rms_max": [round(rms, 3) for rms in rms_maxes],
            "loud": loud,
        })

    report = {
        "takes": takes,
        "asr": ASR_MODEL if transcripts is not None else "off",
        "segments": segment_reports,
    }
    return frame_pcms(chosen, sr, report)


def _memoize_ref_encoder(estimate_fn, model: str) -> None:
    """_estimate_prompt_len re-encodes ref_audio through a bf16 CPU codec on
    every call (~55s). The reference is fixed within a run, so memoize it."""
    cache = estimate_fn.__defaults__[0]
    entry = cache.get(model)
    if not entry:
        return
    speech_tok = entry[2]
    if speech_tok is None or getattr(speech_tok, "_memoized", False):
        return
    memo, orig = {}, speech_tok.encode

    def encode(wav, sr=None, **kw):
        import hashlib

        import numpy as _np

        key = (int(sr or 0), hashlib.blake2b(_np.asarray(wav).tobytes(), digest_size=16).digest())
        if key not in memo:
            memo[key] = orig(wav, sr=sr, **kw)
        return memo[key]

    speech_tok.encode = encode
    speech_tok._memoized = True


def order_request_ids(request_ids, n: int):
    """vllm-omni ids are f"{submission_index}_{uuid4()}". Parse the index rather
    than scraping digits — the uuid is full of them. The permutation check stays
    as the net: a changed id format must abort loudly, never reorder silently."""
    idx = {}
    for r in request_ids:
        head = str(r).split("_", 1)[0]
        if not head.isdigit():
            raise RuntimeError(
                f"unexpected request id format {r!r}; refusing to emit a "
                "possibly-scrambled episode"
            )
        idx[r] = int(head)
    if sorted(idx.values()) != list(range(n)):
        raise RuntimeError(
            f"cannot recover segment order from request ids {sorted(request_ids)}; "
            "refusing to emit a possibly-scrambled episode"
        )
    return sorted(idx, key=lambda r: idx[r])


def _load_prompt_len_estimator(helpers_dir: str):
    """Load end2end.py's _estimate_prompt_len by path. Loading it by file rather
    than by `import` keeps the failure specific: this helper lives only on
    the GPU box and is the one thing that can silently drift out from under a run,
    so say which file and which doc instead of raising a bare ImportError."""
    import importlib.util
    import sys as _sys

    path = os.path.join(helpers_dir, "end2end.py")
    if not os.path.exists(path):
        raise RuntimeError(f"missing remote helper {path}; re-copy it from the "
                           f"pinned vllm-omni checkout, see {HELPER_DOC}")
    if helpers_dir not in _sys.path:
        _sys.path.insert(0, helpers_dir)  # end2end imports its own siblings
    spec = importlib.util.spec_from_file_location("end2end", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        raise RuntimeError(f"remote helper {path} failed to import ({e!r}); "
                           f"see {HELPER_DOC}") from e
    fn = getattr(mod, "_estimate_prompt_len", None)
    if fn is None:
        raise RuntimeError(f"remote helper {path} has no _estimate_prompt_len "
                           f"(vllm-omni drifted); see {HELPER_DOC}")
    return fn


def base_dir() -> str:
    """Where `assets/voice/` and `vllm_omni_helpers/` live.

    Over SSH this file is copied into the synced remote directory alongside
    both, so its own dirname is right. Run on the same machine as the pipeline
    the repo layout differs — this file sits in `tools/remote/` while the
    assets sit at the repo root — so podcast_tts.py passes the root in
    TTS_ASSET_DIR."""
    return os.environ.get("TTS_ASSET_DIR") or os.path.dirname(os.path.abspath(__file__))


def vllm_synth(segments: List[str], lang: str):
    """Real engine. Imports vLLM lazily so this module stays importable in CI."""
    import numpy as np
    import torch

    os.environ.setdefault("VLLM_WORKER_MULTIPROC_METHOD", "spawn")
    here = base_dir()
    _estimate_prompt_len = _load_prompt_len_estimator(
        os.path.join(here, "vllm_omni_helpers"))
    from vllm_omni import Omni

    meta = json.loads(open(os.path.join(here, "assets/voice/ref.json"), encoding="utf-8").read())
    ref_wav = os.path.join(here, f"assets/voice/{lang}-ref.wav")
    m = meta[lang]

    omni = Omni(model=MODEL, gpu_memory_utilization=0.85, max_model_len=8192)
    try:
        inputs = []
        for text in segments:
            info = {
                "task_type": ["Base"],
                "ref_audio": [ref_wav],
                "ref_text": [m["text"]],
                "text": [text],
                "language": [LANGS[lang]],
                "x_vector_only_mode": [False],
                "max_new_tokens": [4096],
            }
            n = _estimate_prompt_len(info, MODEL)
            _memoize_ref_encoder(_estimate_prompt_len, MODEL)
            inputs.append({"prompt_token_ids": [0] * n, "additional_information": info})

        got, sr = {}, SAMPLE_RATE
        for out in omni.generate(inputs):
            mm = out.outputs[0].multimodal_output
            audio = mm["audio"]
            audio = torch.cat(audio, dim=-1) if isinstance(audio, list) else audio
            sr_raw = mm["sr"]
            sr_val = sr_raw[-1] if isinstance(sr_raw, list) and sr_raw else sr_raw
            sr = sr_val.item() if hasattr(sr_val, "item") else int(sr_val)
            f32 = audio.float().cpu().numpy().flatten()
            got[out.request_id] = (
                np.clip(f32, -1.0, 1.0) * 32767
            ).astype("<i2").tobytes()

        order = order_request_ids(got.keys(), len(segments))
        return [got[r] for r in order], sr
    finally:
        # Whisper runs after synthesis and needs the VRAM held by the engine.
        omni.close()


def _reserve_stdout():
    """Save the real fd 1 and repoint fd 1 at fd 2. Must run before vLLM (or
    anything it imports/forks) gets a chance to write a log line to "stdout"
    — that line would land ahead of the framed payload and corrupt it.
    Returns a binary file object wrapping the original fd 1."""
    saved_fd = os.dup(1)
    os.dup2(2, 1)
    return os.fdopen(saved_fd, "wb")


if __name__ == "__main__":
    import sys

    out = _reserve_stdout()
    out.write(run(sys.stdin.buffer.read()))
    out.flush()
