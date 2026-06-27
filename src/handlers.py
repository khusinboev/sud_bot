from __future__ import annotations

import asyncio
import logging
from datetime import datetime

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
from src.scheduler import run_daily_job

logger = logging.getLogger(__name__)
router = Router()

_running_job: asyncio.Task | None = None


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

    excel_buf = build_excel(records)
    fname = filename_now("sud_majlislari")

    await callback.message.answer_document(
        document=BufferedInputFile(excel_buf.getvalue(), filename=fname),
        caption=f"📊 Jami: <b>{len(records)}</b> ta yozuv",
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

    await callback.answer("🔄 Yangilanish boshlandi!")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("⏳ Ma'lumotlar yig'ilmoqda... Bu bir necha daqiqa olishi mumkin.")

    _running_job = asyncio.create_task(
        _run_job_safe(bot, config.allowed_user_ids)
    )


async def _run_job_safe(bot: Bot, user_ids: frozenset[int]) -> None:
    try:
        await run_daily_job(bot, user_ids)
    except Exception as exc:
        logger.exception(f"Job failed: {exc}")
        for uid in user_ids:
            try:
                await bot.send_message(
                    uid,
                    f"❌ Yangilanish xatosi: <code>{exc}</code>",
                    parse_mode="HTML",
                    reply_markup=main_keyboard(),
                )
            except Exception:
                pass


def _format_stats(stats: dict) -> str:
    lines = ["📊 <b>Statistika (aktiv bazadan)</b>\n"]
    lines.append(f"📋 Jami yozuvlar: <b>{stats['total']}</b>")

    dr = stats.get("date_range", (None, None))
    if dr and dr[0]:
        lines.append(f"📅 Davr: {dr[0]} → {dr[1]}")

    if stats.get("by_instance"):
        lines.append("\n<b>Instansiya bo'yicha:</b>")
        for inst, cnt in stats["by_instance"]:
            lines.append(f"  • {inst or '—'}: {cnt}")

    if stats.get("by_court"):
        lines.append("\n<b>Sud bo'yicha (Top 10):</b>")
        for court, cnt in stats["by_court"]:
            lines.append(f"  • {court}: {cnt}")

    if stats.get("by_category"):
        lines.append("\n<b>Kategoriya bo'yicha (Top 5):</b>")
        for cat, cnt in stats["by_category"]:
            short = (cat[:40] + "...") if len(cat) > 40 else cat
            lines.append(f"  • {short}: {cnt}")

    if stats.get("by_date"):
        lines.append("\n<b>Sana bo'yicha (keyingi 10 kun):</b>")
        for d, cnt in stats["by_date"]:
            lines.append(f"  • {d}: {cnt} ta")

    lines.append(f"\n🕐 <i>Yangilangan: {datetime.now().strftime('%d.%m.%Y %H:%M')}</i>")
    return "\n".join(lines)
