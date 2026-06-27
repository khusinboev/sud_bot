from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

KEYWORDS_FILE = Path(__file__).parent / "oliy_talim_keywords.json"

# Apostrofning turli unicode variantlari (', ', ʼ, va h.k.) bitta ko'rinishga keltiriladi
_APOSTROPHE_RE = re.compile(r"[\u2018\u2019\u02bc\u00b4`']")


def _normalize(text: str) -> str:
    """Katta harfga o'tkazadi va apostroflarni olib tashlaydi, taqqoslash uchun."""
    if not text:
        return ""
    text = _APOSTROPHE_RE.sub("", text)
    return text.upper()


@lru_cache(maxsize=1)
def get_keywords() -> list[str]:
    """oliy_talim_keywords.json dan kalit so'zlarni o'qiydi (normallashtirilgan holda)."""
    try:
        raw = json.loads(KEYWORDS_FILE.read_text(encoding="utf-8"))
        return [_normalize(kw) for kw in raw if kw and kw.strip()]
    except Exception as exc:
        logger.warning(f"oliy_talim_keywords.json yuklanmadi: {exc}")
        return []


def is_oliy_talim(record: dict[str, Any]) -> bool:
    """
    Berilgan sud ishi yozuvi oliy ta'limga tegishli muassasaga aloqador
    (vazirlik/universitet/institut) ekanligini category maydoni bo'yicha tekshiradi.
    """
    category = _normalize(str(record.get("category") or ""))
    if not category:
        return False
    return any(kw in category for kw in get_keywords())


def filter_oliy_talim(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ro'yxat ichidan faqat oliy ta'limga tegishli yozuvlarni ajratib qaytaradi."""
    return [r for r in records if is_oliy_talim(r)]