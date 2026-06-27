from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from api_client import fetch_all
from database import batch_upsert, archive_old_records
from excel_export import build_new_records_excel, filename_now

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

TZ_TASHKENT = timezone(timedelta(hours=5))


def now_tashkent() -> datetime:
    return datetime.now(TZ_TASHKENT)


def now_iso() -> str:
    return now_tashkent().isoformat(timespec="seconds")


class _ProgressTracker:
    """
    Birinchi marta xabar yuboradi, keyin o'sha xabarni edit qiladi.
    Agar edit imkonsiz bo'lsa (xabar o'chirilgan, permissions yo'q) — yangi yuboradi.
    """

    def __init__(self, bot: "Bot", user_ids: frozenset[int]):
        self._bot = bot
        self._user_ids = user_ids
        # uid -> message_id
        self._msg_ids: dict[int, int] = {}

    async def update(self, text: str) -> None:
        for uid in self._user_ids:
            existing_id = self._msg_ids.get(uid)
            if existing_id:
                try:
                    await self._bot.edit_message_text(
                        chat_id=uid,
                        message_id=existing_id,
                        text=text,
                    )
                    continue
                except Exception:
                    # Edit failed (too old, deleted, etc.) — fallthrough to send
                    self._msg_ids.pop(uid, None)

            # Send new message and save id
            try:
                sent = await self._bot.send_message(uid, text)
                self._msg_ids[uid] = sent.message_id
            except Exception as exc:
                logger.warning(f"Could not send progress to {uid}: {exc}")

    async def finish(self, text: str, parse_mode: str = "HTML") -> None:
        """Progress xabarini yakuniy natija bilan almashtiradi."""
        for uid in self._user_ids:
            existing_id = self._msg_ids.get(uid)
            if existing_id:
                try:
                    await self._bot.edit_message_text(
                        chat_id=uid,
                        message_id=existing_id,
                        text=text,
                        parse_mode=parse_mode,
                    )
                    continue
                except Exception:
                    self._msg_ids.pop(uid, None)

            try:
                await self._bot.send_message(uid, text, parse_mode=parse_mode)
            except Exception as exc:
                logger.warning(f"Could not send finish msg to {uid}: {exc}")


async def run_daily_job(bot: "Bot", user_ids: frozenset[int]) -> None:
    logger.info("Daily job started")
    start_time = now_tashkent()

    progress = _ProgressTracker(bot, user_ids)

    # Step 1: Archive
    archived_count = await archive_old_records()

    # Step 2: Fetch with live progress (edit same message)
    last_pct: dict[str, int] = {"v": -1}

    async def _on_progress(done: int, total: int, court: str, d: object) -> None:
        pct = int(done / total * 100)
        # update every 20% to avoid Telegram rate limit on edits (1 edit/sec per chat)
        if pct - last_pct["v"] >= 20:
            last_pct["v"] = pct
            await progress.update(f"⏳ Ma'lumot yig'ilmoqda... {pct}% ({done}/{total})")

    records, fetch_stats = await fetch_all(progress_callback=_on_progress)

    if not records:
        logger.warning("No records fetched")
        await progress.finish("⚠️ API dan hech qanday ma'lumot kelmadi.")
        return

    # Step 3: Upsert
    now = now_iso()
    upsert_result = await batch_upsert(records, now)

    new_records = upsert_result["new"]
    updated_records = upsert_result["updated"]
    changed = new_records + updated_records

    elapsed = (now_tashkent() - start_time).seconds // 60

    # Step 4: Result — edit progress message into final summary
    if changed:
        for r in new_records:
            r["_status"] = "new"
        for r in updated_records:
            r["_status"] = "updated"

        summary = _format_changes_summary(new_records, updated_records, fetch_stats, archived_count, elapsed)
        await progress.finish(summary)

        # Excel — always new message (document can't be edited)
        excel_buf = build_new_records_excel(changed)
        fname = filename_now("yangi_majlislar")
        from aiogram.types import BufferedInputFile
        for uid in user_ids:
            try:
                await bot.send_document(
                    uid,
                    document=BufferedInputFile(excel_buf.getvalue(), filename=fname),
                    caption=f"📎 Yangi/o'zgargan {len(changed)} ta yozuv",
                )
            except Exception as exc:
                logger.error(f"Failed to send Excel to {uid}: {exc}")
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
        await progress.finish(summary)

    logger.info(
        f"Daily job done: new={len(new_records)}, updated={len(updated_records)}, "
        f"archived={archived_count}, elapsed={elapsed}m"
    )


def _format_changes_summary(
    new_records: list[dict],
    updated_records: list[dict],
    fetch_stats: dict,
    archived_count: int,
    elapsed: int,
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

    lines.append(
        f"\n📊 So'rovlar: {fetch_stats['total_requests']} | "
        f"🗃 Arxiv: {archived_count} | ⏱ {elapsed} daqiqa"
    )
    return "\n".join(lines)