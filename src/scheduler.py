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

# Faqat shu userga JIDDIY (kritik) API xatoliklari haqida xabar boriladi.
# Boshqa hech qanday userga (shu jumladan .env dagi ALLOWED_USER_ID larga ham) yuborilmaydi.
CRITICAL_ALERT_USER_ID = 1918760732


def now_tashkent() -> datetime:
    return datetime.now(TZ_TASHKENT)


def now_iso() -> str:
    return now_tashkent().isoformat(timespec="seconds")


def now_str() -> str:
    """Foydalanuvchiga ko'rinadigan format: 28.06.2026 09:41"""
    return now_tashkent().strftime("%d.%m.%Y %H:%M")


async def _send_critical_alert(bot: "Bot", text: str) -> None:
    """Faqat CRITICAL_ALERT_USER_ID ga, faqat jiddiy (masalan API butunlay
    ishlamay qolgan) holatlarda yuboriladi."""
    try:
        await bot.send_message(CRITICAL_ALERT_USER_ID, text, parse_mode="HTML")
    except Exception as exc:
        logger.error(f"Could not send critical alert: {exc}")


async def _send_manual_finish(bot: "Bot", user_ids: frozenset[int], success: bool) -> None:
    """Faqat 'Hozir yangilash' tugmasi bilan qo'lda ishga tushirilganda —
    qisqa 1 tadan yakuniy xabar."""
    from src.handlers import main_keyboard  # lazy import — circular import oldini olish uchun

    text = "✅ Yangilanish tugadi." if success else "⚠️ Yangilanish tugadi (xatolik yuz berdi)."
    for uid in user_ids:
        try:
            await bot.send_message(uid, text, reply_markup=main_keyboard())
        except Exception as exc:
            logger.warning(f"Could not send finish msg to {uid}: {exc}")


async def run_daily_job(bot: "Bot", user_ids: frozenset[int], manual: bool = False) -> None:
    """
    Avtomatik kunlik (cron) yoki qo'lda ('Hozir yangilash') ishga tushirilgan yangilanish.

    Xatti-harakat:
    - Kunlik avtomatik: butunlay JIM boshlanadi. Faqat filterga mos yozuvlar
      topilsa — ular bitta Excelga birlashtirilib yuboriladi. Hech qanday
      progress/boshlandi/tugadi xabari YO'Q.
    - Qo'lda ('Hozir yangilash'): faqat 2 ta qisqa xabar — boshlandi (handlers.py
      da) va tugadi (shu yerda, _send_manual_finish orqali).
    - JIDDIY xatolik (API butunlay ishlamay qolsa): faqat CRITICAL_ALERT_USER_ID
      ga qisqa xabar, boshqa hech kimga emas.
    """
    logger.info(f"Daily job started (manual={manual})")

    try:
        archived_count = await archive_old_records()
        records, fetch_stats = await fetch_all()
    except Exception as exc:
        logger.exception(f"Critical failure during fetch: {exc}")
        await _send_critical_alert(
            bot,
            f"❌ <b>Kritik xatolik</b>: ma'lumot olishda muammo yuz berdi.\n<code>{exc}</code>",
        )
        if manual:
            await _send_manual_finish(bot, user_ids, success=False)
        return

    # To'liq API o'chib qolgan holat (barcha so'rovlar xato bergan)
    if fetch_stats["total_requests"] > 0 and fetch_stats["failed"] == fetch_stats["total_requests"]:
        logger.error("Complete API outage detected")
        await _send_critical_alert(
            bot,
            f"❌ <b>API butunlay javob bermayapti.</b>\n"
            f"So'rovlar: {fetch_stats['total_requests']}, barchasi xato.\n"
            f"🕐 {now_str()}",
        )
        if manual:
            await _send_manual_finish(bot, user_ids, success=False)
        return

    if not records:
        logger.info("No records fetched")
        if manual:
            await _send_manual_finish(bot, user_ids, success=True)
        return

    now = now_iso()
    upsert_result = await batch_upsert(records, now)
    new_records = upsert_result["new"]
    updated_records = upsert_result["updated"]
    changed = new_records + updated_records

    if changed:
        for r in new_records:
            r["_status"] = "new"
        for r in updated_records:
            r["_status"] = "updated"

        # Barcha columnlar bo'yicha filterlab, mos kelganlarini bitta Excelga
        # birlashtirib yuboradi. Mos kelmasa — hech narsa yuborilmaydi (jim).
        matched = filter_oliy_talim(changed)
        if matched:
            excel_buf = build_new_records_excel(matched)
            fname = filename_now("oliy_talim")
            from aiogram.types import BufferedInputFile
            for uid in user_ids:
                try:
                    await bot.send_document(
                        uid,
                        document=BufferedInputFile(excel_buf.getvalue(), filename=fname),
                        caption=f"🎓 Filterga mos: <b>{len(matched)}</b> ta yozuv",
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    logger.error(f"Failed to send Excel to {uid}: {exc}")

    logger.info(
        f"Daily job done: new={len(new_records)}, updated={len(updated_records)}, "
        f"archived={archived_count}"
    )

    if manual:
        await _send_manual_finish(bot, user_ids, success=True)
