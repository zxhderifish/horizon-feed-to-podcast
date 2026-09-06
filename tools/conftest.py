"""Run the tools tests against the shipped example config.

`config.toml` holds a real deployment and is gitignored, so tests must not
depend on one existing — a fresh clone has to be green.
"""

import config as app_config

app_config.CONFIG_PATH = app_config.EXAMPLE_PATH
app_config.get.cache_clear()
