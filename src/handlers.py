from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)

from src.config import config
from src.database import get_all_active, get_statistics
from src.excel_export import build_excel, filename_now
from src.filters import filter_oliy_talim
from src.scheduler import run_daily_job

logger = logging.getLogger(__name__)
router = Router()

_running_job: asyncio.Task | None = None

TZ_TASHKENT = timezone(timedelta(hours=5))


def _now_str() -> str:
    return datetime.now(TZ_TASHKENT).strftime("%d.%m.%Y %H:%M")


def _is_allowed(user_id: int) -> bool:
    return user_id in config.allowed_user_ids


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📥 Excel export", callback_data="export"),
            InlineKeyboardButton(text="📊 Statistika", callback_data="stats"),
        ],
        [
            InlineKeyboardButton(text="🔄 Hozir yangilash", callback_data="refresh"),
        ],
    ])


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        await message.answer("⛔ Ruxsat yo'q.")
        return

    await message.answer(
        "🏛 <b>Sud Majlislari Boti</b>\n\n"
        "Har kuni 05:00 da avtomatik yangilanadi.\n"
        "Quyidagi amallardan birini tanlang:",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


@router.callback_query(lambda c: c.data == "export")
async def cb_export(callback: CallbackQuery) -> None:
    """
    Ikkita Excel tayyorlaydi:
    1) Barcha aktiv ma'lumotlar (sud_majlislari_barchasi_...)
    2) Faqat oliy ta'limga tegishlilar (sud_majlislari_oliy_talim_...)
    So'ngra umumiy holat xaqida xabar yuboradi.
    """
    if not _is_allowed(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return

    await callback.answer("⏳ Excel tayyorlanmoqda...")
    await callback.message.edit_reply_markup(reply_markup=None)

    records = await get_all_active()

    if not records:
        await callback.message.answer(
            "📭 Hozircha bazada ma'lumot yo'q.",
            reply_markup=main_keyboard(),
        )
        return

    # 1-Excel: barcha aktiv ma'lumotlar
    all_excel_buf = build_excel(records, sheet_title="Barcha majlislar")
    all_fname = filename_now("sud_majlislari_barchasi")

    await callback.message.answer_document(
        document=BufferedInputFile(all_excel_buf.getvalue(), filename=all_fname),
        caption=f"📋 <b>Barcha ma'lumotlar</b>\nJami: <b>{len(records)}</b> ta yozuv",
        parse_mode="HTML",
    )

    # 2-Excel: faqat oliy ta'limga tegishlilar
    oliy_talim_records = filter_oliy_talim(records)

    if oliy_talim_records:
        oliy_excel_buf = build_excel(oliy_talim_records, sheet_title="Oliy ta'lim")
        oliy_fname = filename_now("sud_majlislari_oliy_talim")

        await callback.message.answer_document(
            document=BufferedInputFile(oliy_excel_buf.getvalue(), filename=oliy_fname),
            caption=f"🎓 <b>Oliy ta'limga tegishli</b>\nJami: <b>{len(oliy_talim_records)}</b> ta yozuv",
            parse_mode="HTML",
        )
    else:
        await callback.message.answer(
            "🎓 Oliy ta'limga tegishli yozuvlar topilmadi.",
        )

    # Umumiy holat xaqida xabar
    stats = await get_statistics()
    summary_text = _format_export_summary(stats, len(records), len(oliy_talim_records))
    await callback.message.answer(
        summary_text,
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


@router.callback_query(lambda c: c.data == "stats")
async def cb_stats(callback: CallbackQuery) -> None:
    if not _is_allowed(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return

    await callback.answer("📊 Yuklanmoqda...")

    stats = await get_statistics()
    text = _format_stats(stats)

    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=main_keyboard())


@router.callback_query(lambda c: c.data == "refresh")
async def cb_refresh(callback: CallbackQuery, bot: Bot) -> None:
    global _running_job

    if not _is_allowed(callback.from_user.id):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return

    if _running_job and not _running_job.done():
        await callback.answer("⚠️ Yangilanish allaqachon davom etmoqda!", show_alert=True)
        return

    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "🔄 <b>Yangilanish boshlandi...</b>",
        parse_mode="HTML",
    )

    _running_job = asyncio.create_task(
        _run_job_safe(bot, config.allowed_user_ids)
    )


async def _run_job_safe(bot: Bot, user_ids: frozenset[int]) -> None:
    # Tugash (yoki xatolik) haqidagi yagona xabarni run_daily_job o'zi
    # manual=True bilan yuboradi — bu yerda qo'shimcha xabar yuborilmaydi.
    try:
        await run_daily_job(bot, user_ids, manual=True)
    except Exception as exc:
        logger.exception(f"Job failed: {exc}")


def _format_stats(stats: dict) -> str:
    """Statistika matnini formatlash (aktiv bazadan)."""
    lines = ["📊 <b>Statistika (aktiv bazadan)</b>\n"]
    lines.append(f"📋 Jami yozuvlar: <b>{stats['total']}</b>")
    lines.append(f"🎓 Filterga mos (oliy ta'lim): <b>{stats['oliy_talim_count']}</b>")

    dr = stats.get("date_range", (None, None))
    if dr and dr[0]:
        lines.append(f"📅 Davr: {dr[0]} → {dr[1]}")
    else:
        # Baza bo'sh — kutilayotgan interval ko'rsatiladi
        lines.append(
            f"📅 Kutilayotgan davr: {stats.get('expected_from', '—')} → {stats.get('expected_to', '—')}"
        )

    lines.append(f"\n🕐 <i>Yangilangan: {_now_str()}</i>")
    return "\n".join(lines)


def _format_export_summary(stats: dict, total_count: int, oliy_talim_count: int) -> str:
    """Excel export tugagandan keyingi umumiy holat xabari."""
    lines = ["📈 <b>Umumiy holat</b>\n"]
    lines.append(f"📋 Bazadagi jami yozuvlar: <b>{total_count}</b>")
    lines.append(f"🎓 Oliy ta'limga tegishli: <b>{oliy_talim_count}</b>")

    dr = stats.get("date_range", (None, None))
    if dr and dr[0]:
        lines.append(f"📅 Davr: {dr[0]} → {dr[1]}")

    lines.append(f"\n🕐 <i>Eksport vaqti: {_now_str()}</i>")
    return "\n".join(lines)