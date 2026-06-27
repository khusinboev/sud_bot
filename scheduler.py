from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from api_client import fetch_all
from database import batch_upsert, archive_old_records, get_all_active
from excel_export import build_new_records_excel, build_excel, filename_now

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

# UTC+5 Tashkent
TZ_TASHKENT = timezone(timedelta(hours=5))


def now_tashkent() -> datetime:
    return datetime.now(TZ_TASHKENT)


def now_iso() -> str:
    return now_tashkent().isoformat(timespec="seconds")


async def run_daily_job(bot: Bot, user_ids: frozenset[int], progress_msg_ids: dict | None = None) -> None:
    """
    Main daily job:
    1. Archive old records
    2. Fetch 30-day window
    3. Upsert into DB
    4. Notify users about new/updated + send Excel
    5. Send summary stats
    """
    logger.info("Daily job started")
    start_time = now_tashkent()

    # Step 1: Archive
    archived_count = await archive_old_records()

    # Step 2: Fetch
    # Progress callback — sends occasional Telegram updates
    progress_cache: dict[str, int] = {"last_reported": 0}

    async def _progress(done: int, total: int, court: str, d: object) -> None:
        pct = int(done / total * 100)
        if pct - progress_cache["last_reported"] >= 20:
            progress_cache["last_reported"] = pct
            msg = f"⏳ Ma'lumot yig'ilmoqda... {pct}% ({done}/{total})"
            for uid in user_ids:
                try:
                    await bot.send_message(uid, msg)
                except Exception:
                    pass

    records, fetch_stats = await fetch_all(progress_callback=_progress)

    if not records:
        logger.warning("No records fetched")
        for uid in user_ids:
            try:
                await bot.send_message(uid, "⚠️ API dan hech qanday ma'lumot kelmadi.")
            except Exception:
                pass
        return

    # Step 3: Upsert
    now = now_iso()
    upsert_result = await batch_upsert(records, now)

    new_records = upsert_result["new"]
    updated_records = upsert_result["updated"]
    changed = new_records + updated_records

    elapsed = (now_tashkent() - start_time).seconds // 60

    # Step 4: Notify if changes
    if changed:
        # Tag records with status for Excel coloring
        for r in new_records:
            r["_status"] = "new"
        for r in updated_records:
            r["_status"] = "updated"

        change_text = _format_changes_summary(new_records, updated_records, fetch_stats)

        excel_buf = build_new_records_excel(changed)
        fname = filename_now("yangi_majlislar")

        for uid in user_ids:
            try:
                await bot.send_message(uid, change_text, parse_mode="HTML")
                from aiogram.types import BufferedInputFile
                await bot.send_document(
                    uid,
                    document=BufferedInputFile(excel_buf.getvalue(), filename=fname),
                    caption=f"📎 Yangi/o'zgargan {len(changed)} ta yozuv",
                )
            except Exception as exc:
                logger.error(f"Failed to notify user {uid}: {exc}")
    else:
        summary = (
            f"✅ <b>Yangilanish tugadi</b>\n\n"
            f"🔍 So'rovlar: {fetch_stats['total_requests']}\n"
            f"📦 Jami topilgan: {fetch_stats['unique_records']}\n"
            f"🆕 Yangi: 0\n"
            f"✏️ O'zgargan: 0\n"
            f"🗃 Arxivlangan: {archived_count}\n"
            f"⏱ Vaqt: {elapsed} daqiqa"
        )
        for uid in user_ids:
            try:
                await bot.send_message(uid, summary, parse_mode="HTML")
            except Exception as exc:
                logger.error(f"Failed to send summary to {uid}: {exc}")

    logger.info(
        f"Daily job done: new={len(new_records)}, updated={len(updated_records)}, "
        f"archived={archived_count}, elapsed={elapsed}m"
    )


def _format_changes_summary(
    new_records: list[dict],
    updated_records: list[dict],
    fetch_stats: dict,
) -> str:
    lines = ["📬 <b>Yangi ma'lumotlar topildi!</b>\n"]

    if new_records:
        lines.append(f"🆕 <b>Yangi ishlar: {len(new_records)} ta</b>")
        for r in new_records[:5]:
            lines.append(
                f"  • <code>{r.get('casenumber', '—')}</code> | "
                f"{r.get('hearing_date', '')} {r.get('hearing_time', '')} | "
                f"{r.get('globalid', '')}"
            )
        if len(new_records) > 5:
            lines.append(f"  <i>... va yana {len(new_records) - 5} ta (Excel da to'liq)</i>")

    if updated_records:
        lines.append(f"\n✏️ <b>O'zgargan ishlar: {len(updated_records)} ta</b>")
        for r in updated_records[:3]:
            lines.append(
                f"  • <code>{r.get('casenumber', '—')}</code> | "
                f"{r.get('hearing_date', '')} {r.get('hearing_time', '')}"
            )
        if len(updated_records) > 3:
            lines.append(f"  <i>... va yana {len(updated_records) - 3} ta</i>")

    lines.append(f"\n📊 Jami so'rovlar: {fetch_stats['total_requests']}")
    return "\n".join(lines)
