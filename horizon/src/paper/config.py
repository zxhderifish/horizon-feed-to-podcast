"""Paper radar configuration — decoupled from the shared news Config."""

import json
from pathlib import Path
from typing import List

from pydantic import BaseModel, Field

from ..models import AIConfig
from ..storage.manager import _expand_env_vars


class ArxivConfig(BaseModel):
    categories: List[str] = Field(default_factory=lambda: ["cs.DC", "cs.LG"])
    keywords: List[str] = Field(default_factory=list)
    max_results: int = 100


class PaperConfig(BaseModel):
    ai: AIConfig
    arxiv: ArxivConfig = Field(default_factory=ArxivConfig)
    score_threshold: float = 7.0
    time_window_days: int = 7
    language: str = "zh"  # "zh" or "en"


def load_paper_config(path: Path) -> PaperConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Paper config not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    data = _expand_env_vars(data)
    return PaperConfig.model_validate(data)
