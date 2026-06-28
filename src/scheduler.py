from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from src.api_client import fetch_all
from src.database import batch_upsert, archive_old_records
from src.excel_export import build_new_records_excel, filename_now
from src.filters import filter_oliy_talim

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

TZ_TASHKENT = timezone(timedelta(hours=5))


def now_tashkent() -> datetime:
    return datetime.now(TZ_TASHKENT)


def now_iso() -> str:
    return now_tashkent().isoformat(timespec="seconds")


def now_str() -> str:
    """Foydalanuvchiga ko'rinadigan format: 28.06.2026 09:41"""
    return now_tashkent().strftime("%d.%m.%Y %H:%M")


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
    """
    Avtomatik kunlik yoki qo'lda ishga tushirilgan yangilanish.
    Kelasi 30 kunlik sud ishlarini API dan yig'adi, bazaga saqlaydi,
    yangi/o'zgarganlarni Excel bilan yuboradi.
    """
    logger.info("Daily job started")
    start_time = now_tashkent()

    progress = _ProgressTracker(bot, user_ids)

    # Step 1: Archive (o'tgan sanalarni arxivga ko'chirish)
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
        await progress.finish(
            f"⚠️ <b>API dan hech qanday ma'lumot kelmadi.</b>\n\n"
            f"🕐 <i>Tekshirilgan: {now_str()}</i>"
        )
        return

    # Step 3: Upsert
    now = now_iso()
    upsert_result = await batch_upsert(records, now)

    new_records = upsert_result["new"]
    updated_records = upsert_result["updated"]
    unchanged_count = len(upsert_result["unchanged"])
    changed = new_records + updated_records

    elapsed = max(1, (now_tashkent() - start_time).seconds // 60)

    # Step 4: Result
    if changed:
        for r in new_records:
            r["_status"] = "new"
        for r in updated_records:
            r["_status"] = "updated"

        # Oliy ta'limga tegishli — faqat YANGI qo'shilganlar ichidan
        oliy_talim_records = filter_oliy_talim(new_records)

        summary = _format_changes_summary(
            new_records, updated_records, fetch_stats, archived_count,
            unchanged_count, elapsed,
            oliy_talim_count=len(oliy_talim_records),
        )
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
                    caption=f"📎 Yangi/o'zgargan <b>{len(changed)}</b> ta yozuv",
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.error(f"Failed to send Excel to {uid}: {exc}")

        # Oliy ta'limga tegishli — alohida xabar + Excel
        if oliy_talim_records:
            oliy_talim_summary = _format_oliy_talim_summary(oliy_talim_records)
            oliy_talim_buf = build_new_records_excel(oliy_talim_records)
            oliy_talim_fname = filename_now("oliy_talim")
            for uid in user_ids:
                try:
                    await bot.send_message(uid, oliy_talim_summary, parse_mode="HTML")
                    await bot.send_document(
                        uid,
                        document=BufferedInputFile(oliy_talim_buf.getvalue(), filename=oliy_talim_fname),
                        caption=f"🎓 Yangi ishlar ichidan oliy ta'limga tegishli: <b>{len(oliy_talim_records)}</b> ta",
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    logger.error(f"Failed to send oliy_talim Excel to {uid}: {exc}")
    else:
        summary = (
            f"✅ <b>Yangilanish tugadi</b>\n\n"
            f"📅 Davr: {fetch_stats['date_from']} → {fetch_stats['date_to']}\n"
            f"🔍 So'rovlar: {fetch_stats['total_requests']} "
            f"(muvaffaqiyatli: {fetch_stats['successful']}, xato: {fetch_stats['failed']})\n"
            f"📦 API dan keldi: {fetch_stats['unique_records']} ta\n"
            f"🆕 Yangi: 0\n"
            f"✏️ O'zgargan: 0\n"
            f"📌 O'zgarishsiz: {unchanged_count}\n"
            f"🗃 Arxivlangan: {archived_count}\n"
            f"⏱ Vaqt: {elapsed} daqiqa\n\n"
            f"🕐 <i>Yangilangan: {now_str()}</i>"
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
    unchanged_count: int,
    elapsed: int,
    oliy_talim_count: int = 0,
) -> str:
    lines = ["📬 <b>Yangi ma'lumotlar topildi!</b>\n"]
    lines.append(f"📅 Davr: {fetch_stats['date_from']} → {fetch_stats['date_to']}")

    if new_records:
        lines.append(f"\n🆕 <b>Yangi ishlar: {len(new_records)} ta</b>")
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

    if oliy_talim_count:
        lines.append(f"\n🎓 <b>Yangi ishlar ichidan oliy ta'limga tegishli: {oliy_talim_count} ta</b> (quyida alohida)")

    lines.append(
        f"\n📊 API dan keldi: {fetch_stats['unique_records']} ta | "
        f"📌 O'zgarishsiz: {unchanged_count} | "
        f"🗃 Arxiv: {archived_count} | ⏱ {elapsed} daqiqa"
    )
    lines.append(f"\n🕐 <i>Yangilangan: {now_str()}</i>")
    return "\n".join(lines)


def _format_oliy_talim_summary(records: list[dict]) -> str:
    lines = ["🎓 <b>Yangi qo'shilganlar ichidan oliy ta'limga tegishli ishlar</b>\n"]
    lines.append(f"Jami: <b>{len(records)} ta</b>\n")

    for r in records[:10]:
        lines.append(
            f"🆕 <code>{r.get('casenumber', '—')}</code> | "
            f"{r.get('hearing_date', '')} {r.get('hearing_time', '')}\n"
            f"   <i>{r.get('category', '—')}</i>"
        )
    if len(records) > 10:
        lines.append(f"\n<i>... va yana {len(records) - 10} ta (Excel da to'liq)</i>")

    return "\n".join(lines)
