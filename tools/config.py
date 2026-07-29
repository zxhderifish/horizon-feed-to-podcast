"""Load site/podcast settings from config.toml.

Everything that is specific to *your* deployment — domains, bucket, show names,
owner email — lives in config.toml at the repo root. Copy config.example.toml
to config.toml and edit it; config.toml is gitignored.
"""

import os
import tomllib
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("HORIZON_CONFIG", ROOT / "config.toml"))
EXAMPLE_PATH = ROOT / "config.example.toml"


def load(path: Path = None) -> dict:
    """Read a config file and derive per-show URLs from the hosting block."""
    path = Path(path) if path else CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — copy config.example.toml to config.toml and edit it "
            f"(or point HORIZON_CONFIG at your own file)."
        )
    with open(path, "rb") as f:
        cfg = tomllib.load(f)

    site = cfg["hosting"]["site_url"].rstrip("/")
    for lang, show in cfg["podcast"]["shows"].items():
        show.setdefault("cover", f"{site}/covers/cover-{lang}.jpg")
        show.setdefault("feed_url", f"{site}/podcast-{lang}.xml")
        show.setdefault("link", site)
    return cfg


@lru_cache(maxsize=1)
def get() -> dict:
    """Cached config for normal runs. Tests call load() with an explicit path."""
    return load()
