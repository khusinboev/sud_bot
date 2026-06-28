from __future__ import annotations

import logging
from pathlib import Path
from datetime import date, timedelta, timezone
from typing import Any

import aiosqlite

from src.config import config
from src.filters import filter_oliy_talim

logger = logging.getLogger(__name__)

TZ_TASHKENT = timezone(timedelta(hours=5))

CREATE_CASES_TABLE = """
CREATE TABLE IF NOT EXISTS cases (
    case_id       TEXT PRIMARY KEY,
    casenumber    TEXT,
    hearing_date  TEXT,
    hearing_time  TEXT,
    responsible   TEXT,
    instance      TEXT,
    parentregionid TEXT,
    regionid      TEXT,
    globalid      TEXT,
    claimkind     TEXT,
    claimtype     TEXT,
    category      TEXT,
    claiment      TEXT,
    defendant     TEXT,
    representing_org TEXT,
    first_seen    TEXT NOT NULL,
    last_updated  TEXT NOT NULL
)
"""

CREATE_CHANGELOG_TABLE = """
CREATE TABLE IF NOT EXISTS changelog (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     TEXT NOT NULL,
    field_name  TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    changed_at  TEXT NOT NULL
)
"""


TRACKED_FIELDS = (
    "casenumber", "hearing_date", "hearing_time",
    "responsible", "instance", "category",
    "claiment", "defendant",
)

CASE_FIELDS = (
    "case_id", "casenumber", "hearing_date", "hearing_time",
    "responsible", "instance", "parentregionid", "regionid",
    "globalid", "claimkind", "claimtype", "category",
    "claiment", "defendant", "representing_org",
)


def _today_tashkent() -> date:
    from datetime import datetime
    return datetime.now(TZ_TASHKENT).date()


async def init_db(db_path: Path) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(CREATE_CASES_TABLE)
        await db.execute(CREATE_CHANGELOG_TABLE)
        await db.commit()


async def init_all() -> None:
    await init_db(config.active_db_path)
    await init_db(config.archive_db_path)


def _row_to_dict(row: aiosqlite.Row, keys: tuple[str, ...]) -> dict[str, Any]:
    return dict(zip(keys, row))


async def upsert_case(db: aiosqlite.Connection, record: dict[str, Any], now: str) -> str:
    """
    Returns: 'new' | 'updated' | 'unchanged'
    """
    case_id = record["case_id"]

    async with db.execute(
        f"SELECT {', '.join(CASE_FIELDS)}, first_seen FROM cases WHERE case_id = ?",
        (case_id,),
    ) as cursor:
        existing = await cursor.fetchone()

    if existing is None:
        await db.execute(
            f"""
            INSERT INTO cases ({', '.join(CASE_FIELDS)}, first_seen, last_updated)
            VALUES ({', '.join('?' * len(CASE_FIELDS))}, ?, ?)
            """,
            [record.get(f) for f in CASE_FIELDS] + [now, now],
        )
        return "new"

    # Check for changes in tracked fields
    existing_dict = _row_to_dict(existing, CASE_FIELDS + ("first_seen",))
    changes: list[tuple[str, str | None, str | None]] = []

    for field in TRACKED_FIELDS:
        old_val = existing_dict.get(field)
        new_val = record.get(field)
        if old_val != new_val:
            changes.append((field, old_val, new_val))

    if not changes:
        return "unchanged"

    # Log changes
    for field_name, old_val, new_val in changes:
        await db.execute(
            "INSERT INTO changelog (case_id, field_name, old_value, new_value, changed_at) VALUES (?,?,?,?,?)",
            (case_id, field_name, old_val, new_val, now),
        )

    update_fields = ", ".join(f"{f} = ?" for f in TRACKED_FIELDS)
    await db.execute(
        f"UPDATE cases SET {update_fields}, last_updated = ? WHERE case_id = ?",
        [record.get(f) for f in TRACKED_FIELDS] + [now, case_id],
    )
    return "updated"


async def archive_old_records() -> int:
    """Move records with hearing_date < today (Tashkent) to archive db. Returns count moved."""
    today_str = _today_tashkent().isoformat()
    archived = 0

    async with aiosqlite.connect(config.active_db_path) as active_db:
        async with active_db.execute(
            "SELECT * FROM cases WHERE hearing_date < ?", (today_str,)
        ) as cursor:
            old_rows = await cursor.fetchall()
            col_names = [d[0] for d in cursor.description]

        if not old_rows:
            return 0

        async with aiosqlite.connect(config.archive_db_path) as arch_db:
            for row in old_rows:
                row_dict = dict(zip(col_names, row))
                cols = ", ".join(row_dict.keys())
                placeholders = ", ".join("?" * len(row_dict))
                await arch_db.execute(
                    f"INSERT OR REPLACE INTO cases ({cols}) VALUES ({placeholders})",
                    list(row_dict.values()),
                )
            await arch_db.commit()

        case_ids = [row[col_names.index("case_id")] for row in old_rows]
        await active_db.execute(
            f"DELETE FROM cases WHERE case_id IN ({','.join('?' * len(case_ids))})",
            case_ids,
        )
        await active_db.commit()
        archived = len(old_rows)

    logger.info(f"Archived {archived} old records")
    return archived


async def batch_upsert(records: list[dict[str, Any]], now: str) -> dict[str, list[dict]]:
    """
    Returns dict with keys: 'new', 'updated', 'unchanged'
    Each value is list of records.
    """
    result: dict[str, list[dict]] = {"new": [], "updated": [], "unchanged": []}

    async with aiosqlite.connect(config.active_db_path) as db:
        for record in records:
            status = await upsert_case(db, record, now)
            result[status].append(record)
        await db.commit()

    return result


async def get_all_active() -> list[dict[str, Any]]:
    async with aiosqlite.connect(config.active_db_path) as db:
        async with db.execute(
            "SELECT * FROM cases ORDER BY hearing_date, hearing_time"
        ) as cursor:
            rows = await cursor.fetchall()
            col_names = [d[0] for d in cursor.description]
    return [dict(zip(col_names, row)) for row in rows]


async def get_statistics() -> dict[str, Any]:
    """
    Aktiv bazadagi barcha ma'lumotlar bo'yicha statistika.
    date_range kelasi 30 kunlik interval hisobida ko'rsatiladi.
    """
    from datetime import datetime

    today = _today_tashkent()
    date_from = today + timedelta(days=1)
    date_to = today + timedelta(days=30)

    async with aiosqlite.connect(config.active_db_path) as db:
        # Jami aktiv yozuvlar
        async with db.execute("SELECT COUNT(*) FROM cases") as cur:
            total = (await cur.fetchone())[0]

        # Aslida bazadagi sana diapazoni
        async with db.execute(
            "SELECT MIN(hearing_date), MAX(hearing_date) FROM cases"
        ) as cur:
            db_date_range = await cur.fetchone()

    all_active = await get_all_active()
    oliy_talim_count = len(filter_oliy_talim(all_active))

    # Sana diapazoni: avval DB dagi real qiymatni formatlash
    dr_min, dr_max = db_date_range if db_date_range else (None, None)
    if dr_min:
        try:
            dr_min_fmt = datetime.strptime(dr_min, "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            dr_min_fmt = dr_min
    else:
        dr_min_fmt = date_from.strftime("%d.%m.%Y")

    if dr_max:
        try:
            dr_max_fmt = datetime.strptime(dr_max, "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            dr_max_fmt = dr_max
    else:
        dr_max_fmt = date_to.strftime("%d.%m.%Y")

    return {
        "total": total,
        "oliy_talim_count": oliy_talim_count,
        "date_range": (dr_min_fmt, dr_max_fmt),
        "expected_from": date_from.strftime("%d.%m.%Y"),
        "expected_to": date_to.strftime("%d.%m.%Y"),
    }
