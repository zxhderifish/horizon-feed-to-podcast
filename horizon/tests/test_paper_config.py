import json

from src.paper.config import PaperConfig, load_paper_config
from src.models import AIProvider


def _write(tmp_path, data):
    p = tmp_path / "config.paper.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_load_paper_config_expands_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_KEY_ENV", "ANTHROPIC_API_KEY")
    path = _write(tmp_path, {
        "ai": {"provider": "anthropic", "model": "claude-sonnet-4.5-20250929",
               "api_key_env": "${MY_KEY_ENV}"},
        "arxiv": {"categories": ["cs.DC"], "keywords": ["FSDP"], "max_results": 50},
        "score_threshold": 7.0,
    })
    cfg = load_paper_config(path)
    assert isinstance(cfg, PaperConfig)
    assert cfg.ai.provider == AIProvider.ANTHROPIC
    assert cfg.ai.api_key_env == "ANTHROPIC_API_KEY"  # ${...} expanded
    assert cfg.arxiv.keywords == ["FSDP"]
    assert cfg.score_threshold == 7.0


def test_score_threshold_defaults_to_7(tmp_path):
    path = _write(tmp_path, {
        "ai": {"provider": "anthropic", "model": "m", "api_key_env": "ANTHROPIC_API_KEY"},
        "arxiv": {"categories": ["cs.DC"]},
    })
    cfg = load_paper_config(path)
    assert cfg.score_threshold == 7.0
    assert cfg.language == "zh"
