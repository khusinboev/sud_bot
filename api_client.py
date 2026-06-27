from __future__ import annotations

import asyncio
import logging
import random
from datetime import date, timedelta
from typing import Any

import aiohttp

from config import config
from regions import get_active_court_names

logger = logging.getLogger(__name__)

BASE_URL = "https://apivka.sud.uz/works/getWorkData"

# Per-session timeout
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; SudBot/1.0)",
}


async def _fetch_one(
    session: aiohttp.ClientSession,
    court_type: str,
    court_name: str,
    target_date: date,
    retries: int = 3,
) -> "list[dict[str, Any]] | None":
    """
    Returns:
        list  — success (may be empty [] if no hearings that day)
        None  — real failure (HTTP error / timeout after all retries)
    """
    params = {
        "court_type": court_type,
        "court_name": court_name,
        "year": target_date.year,
        "month": target_date.month,
        "day": target_date.day,
    }

    for attempt in range(1, retries + 1):
        try:
            async with session.get(BASE_URL, params=params, headers=HEADERS) as resp:
                if resp.status == 429:
                    wait = 60 * attempt
                    logger.warning(f"Rate limited on {court_name} {target_date}, waiting {wait}s")
                    await asyncio.sleep(wait)
                    continue

                if resp.status != 200:
                    logger.warning(
                        f"HTTP {resp.status} for {court_name} {target_date} (attempt {attempt})"
                    )
                    if attempt < retries:
                        await asyncio.sleep(5 * attempt)
                    continue

                data = await resp.json(content_type=None)

                # null = no hearings scheduled for this date — valid empty response
                if data is None:
                    logger.debug(f"Empty day (null): {court_name} {target_date}")
                    return []

                if not isinstance(data, list):
                    logger.warning(
                        f"Unexpected response type for {court_name} {target_date}: "
                        f"{type(data).__name__} = {str(data)[:100]}"
                    )
                    return []

                return data

        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning(f"Request error {court_name} {target_date} attempt {attempt}: {exc}")
            if attempt < retries:
                await asyncio.sleep(5 * attempt)

    logger.error(f"All {retries} attempts failed for {court_name} {target_date}")
    return None


async def fetch_all(
    progress_callback: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Fetch 30 days from today for all active courts.
    Returns: (all_records, stats)
    stats = {total_requests, successful, failed, total_records}
    """
    courts = get_active_court_names()
    today = date.today()
    dates = [today + timedelta(days=i) for i in range(30)]

    total_requests = len(courts) * len(dates)
    completed = 0
    failed = 0
    all_records: list[dict[str, Any]] = []

    connector = aiohttp.TCPConnector(limit=1)  # sequential to avoid rate limits
    async with aiohttp.ClientSession(
        connector=connector, timeout=REQUEST_TIMEOUT
    ) as session:
        for court_name in courts:
            for target_date in dates:
                result = await _fetch_one(
                    session, config.court_type, court_name, target_date
                )

                if result is None:
                    # Real network/HTTP failure
                    failed += 1
                else:
                    # [] = valid empty day, [..] = has hearings
                    all_records.extend(result)
                    completed += 1

                if progress_callback:
                    await progress_callback(completed, total_requests, court_name, target_date)

                # Throttle
                delay = random.uniform(config.request_delay_min, config.request_delay_max)
                await asyncio.sleep(delay)

    # Deduplicate by case_id (API may return duplicates)
    seen: set[str] = set()
    unique_records: list[dict[str, Any]] = []
    for r in all_records:
        cid = r.get("case_id")
        if cid and cid not in seen:
            seen.add(cid)
            unique_records.append(r)

    stats = {
        "total_requests": total_requests,
        "successful": completed,
        "failed": failed,
        "raw_records": len(all_records),
        "unique_records": len(unique_records),
    }
    logger.info(f"Fetch complete: {stats}")
    return unique_records, stats