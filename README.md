# Sud Majlislari Bot

Telegram bot: iqtisodiy sud majlislarini kuzatish (apivka.sud.uz).

## O'rnatish

```bash
git clone ...
cd sud_bot
pip install -r requirements.txt
cp .env.example .env
# .env faylini tahrirlang
python bot.py
```

## .env sozlamalari

| Kalit | Tavsif | Misol |
|-------|--------|-------|
| `BOT_TOKEN` | BotFather tokeni | `123456:ABC...` |
| `ALLOWED_USER_IDS` | Telegram user IDlar (vergul bilan) | `111111,222222` |
| `COURT_TYPE` | Sud turi | `iqtisodiy` |
| `REGION_MODE` | `tashkent` yoki `all` | `tashkent` |
| `SCHEDULE_HOUR` | Kunlik ishga tushish soati (Toshkent vaqti) | `5` |
| `SCHEDULE_MINUTE` | Daqiqa | `0` |
| `REQUEST_DELAY_MIN` | So'rovlar orasidagi min kutish (soniya) | `3` |
| `REQUEST_DELAY_MAX` | So'rovlar orasidagi max kutish (soniya) | `7` |
| `ACTIVE_DB_PATH` | Aktiv ma'lumotlar bazasi yo'li | `data/active.db` |
| `ARCHIVE_DB_PATH` | Arxiv ma'lumotlar bazasi yo'li | `data/archive.db` |

## REGION_MODE haqida

- **`tashkent`**: faqat `tashxsud` + `toshkent.t` (2 sud × 30 kun = 60 so'rov/kun)
- **`all`**: barcha 29 ta sud (29 × 30 = 870 so'rov/kun, ~1.5-2 soat)

## Bot tugmalari

| Tugma | Vazifasi |
|-------|---------|
| 📥 Excel export | Aktiv bazadan barcha yozuvlarni Excel qilib yuboradi |
| 📊 Statistika | Xabarni yangilab statistik ma'lumot ko'rsatadi |
| 🔄 Hozir yangilash | Scheduler kutmasdan hoziroq ishga tushiradi |

## Fayl tuzilishi

```
sud_bot/
├── bot.py           # Entry point, scheduler
├── config.py        # .env parser
├── database.py      # SQLite CRUD, arxivlash
├── api_client.py    # aiohttp, rate limiting
├── regions.py       # court_name ro'yxati
├── handlers.py      # aiogram handlers
├── excel_export.py  # openpyxl export
├── regions.json     # barcha hudud ma'lumotlari
├── requirements.txt
├── .env.example
└── data/            # SQLite fayllari (auto-yaratiladi)
    ├── active.db
    └── archive.db
```

## Arxivlash mantiqi

- Har kuni job boshlanishida `hearing_date < bugun` bo'lgan yozuvlar
  `archive.db` ga ko'chiriladi va `active.db` dan o'chiriladi
- `active.db` — faqat bugun va undan keyingi 30 kun
- Ikki DB ham bir xil sxemaga ega

## VPS uchun systemd

```ini
[Unit]
Description=Sud Bot
After=network.target

[Service]
WorkingDirectory=/opt/sud_bot
ExecStart=/usr/bin/python3 bot.py
Restart=always
RestartSec=10
User=ubuntu

[Install]
WantedBy=multi-user.target
```
# sud_bot
