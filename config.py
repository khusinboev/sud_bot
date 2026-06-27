from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _required(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


@dataclass(frozen=True)
class Config:
    bot_token: str
    allowed_user_ids: frozenset[int]
    court_type: str
    region_mode: str  # "tashkent" | "all"
    schedule_hour: int
    schedule_minute: int
    request_delay_min: float
    request_delay_max: float
    active_db_path: Path
    archive_db_path: Path

    # Tashkent-specific court_names (always used when region_mode=tashkent)
    TASHKENT_COURTS: tuple[str, ...] = field(
        default=("tashxsud", "toshkent.t"), init=False
    )

    @classmethod
    def from_env(cls) -> "Config":
        raw_ids = _required("ALLOWED_USER_IDS")
        user_ids = frozenset(int(uid.strip()) for uid in raw_ids.split(",") if uid.strip())

        active_db = Path(os.getenv("ACTIVE_DB_PATH", "data/active.db"))
        archive_db = Path(os.getenv("ARCHIVE_DB_PATH", "data/archive.db"))
        active_db.parent.mkdir(parents=True, exist_ok=True)

        return cls(
            bot_token=_required("BOT_TOKEN"),
            allowed_user_ids=user_ids,
            court_type=os.getenv("COURT_TYPE", "iqtisodiy"),
            region_mode=os.getenv("REGION_MODE", "tashkent").lower(),
            schedule_hour=int(os.getenv("SCHEDULE_HOUR", "5")),
            schedule_minute=int(os.getenv("SCHEDULE_MINUTE", "0")),
            request_delay_min=float(os.getenv("REQUEST_DELAY_MIN", "3")),
            request_delay_max=float(os.getenv("REQUEST_DELAY_MAX", "7")),
            active_db_path=active_db,
            archive_db_path=archive_db,
        )


config = Config.from_env()
