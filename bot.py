from __future__ import annotations

import asyncio
import logging
import sys
from datetime import timezone, timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import config
from src.database import init_all
from src.handlers import router
from src.scheduler import run_daily_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

TZ_TASHKENT = timezone(timedelta(hours=5))


async def main() -> None:
    await init_all()
    logger.info("Databases initialized")

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    # Scheduler setup (Tashkent timezone)
    scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")
    scheduler.add_job(
        run_daily_job,
        trigger=CronTrigger(
            hour=config.schedule_hour,
            minute=config.schedule_minute,
            timezone="Asia/Tashkent",
        ),
        kwargs={"bot": bot, "user_ids": config.allowed_user_ids},
        id="daily_job",
        replace_existing=True,
        misfire_grace_time=600,  # 10 min grace if server was down
    )
    scheduler.start()
    logger.info(
        f"Scheduler started: daily at {config.schedule_hour:02d}:{config.schedule_minute:02d} Tashkent"
    )

    # Notify users bot is online
    for uid in config.allowed_user_ids:
        try:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            from src.handlers import main_keyboard
            await bot.send_message(
                uid,
                f"🤖 <b>Bot ishga tushdi</b>\n"
                f"⏰ Kunlik yangilanish: {config.schedule_hour:02d}:{config.schedule_minute:02d} (Toshkent)\n"
                f"🌍 Hudud rejimi: <code>{config.region_mode}</code>",
                reply_markup=main_keyboard(),
            )
        except Exception as exc:
            logger.warning(f"Could not notify user {uid}: {exc}")

    logger.info("Starting polling...")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        scheduler.shutdown()
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
