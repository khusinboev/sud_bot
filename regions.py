from __future__ import annotations

import json
from pathlib import Path
from functools import lru_cache

from config import config

REGIONS_FILE = Path(__file__).parent / "regions.json"

TASHKENT_COURTS: list[str] = ["tashxsud", "toshkent.t"]


@lru_cache(maxsize=1)
def get_all_court_names() -> list[str]:
    data = json.loads(REGIONS_FILE.read_text(encoding="utf-8"))
    courts: list[str] = []
    for entry in data:
        for region in entry.get("type_court", []):
            for t in region.get("tumanlar", []):
                value = t.get("value")
                if value:
                    courts.append(value)
    return courts


def get_active_court_names() -> list[str]:
    if config.region_mode == "tashkent":
        return TASHKENT_COURTS
    return get_all_court_names()
