from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

WEEKDAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def _load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _parse_ids(value: str) -> frozenset[int]:
    ids: set[int] = set()
    for chunk in value.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError as exc:
            raise SystemExit(f"Некорректный ID администратора: {chunk!r}") from exc
    return frozenset(ids)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} должен быть числом, получено: {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise SystemExit(f"{name} должен быть в диапазоне {minimum}..{maximum}, получено: {value}")
    return value


@dataclass(frozen=True)
class Config:
    bot_token: str
    group_chat_id: int | None
    admin_ids: frozenset[int]
    timezone: ZoneInfo
    poll_hour: int
    poll_minute: int
    weekly_report_day: str
    weekly_report_hour: int
    weekly_report_minute: int
    motivation_day: str
    motivation_hour: int
    motivation_minute: int
    retention_days: int
    db_path: str


def load_config() -> Config:
    _load_dotenv()

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN не задан. Укажите его в .env или переменных окружения.")

    raw_chat = os.getenv("GROUP_CHAT_ID", "").strip()
    group_chat_id: int | None = None
    if raw_chat:
        try:
            group_chat_id = int(raw_chat)
        except ValueError as exc:
            raise SystemExit(f"GROUP_CHAT_ID должен быть числом, получено: {raw_chat!r}") from exc

    tz_name = os.getenv("TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"
    try:
        timezone = ZoneInfo(tz_name)
    except Exception as exc:
        raise SystemExit(f"Неизвестный часовой пояс {tz_name!r}: {exc}") from exc

    weekly_report_day = os.getenv("WEEKLY_REPORT_DAY", "sun").strip().lower()
    if weekly_report_day not in WEEKDAYS:
        raise SystemExit(
            f"WEEKLY_REPORT_DAY должен быть одним из {', '.join(sorted(WEEKDAYS))}, "
            f"получено: {weekly_report_day!r}"
        )

    motivation_day = os.getenv("MOTIVATION_DAY", "mon").strip().lower()
    if motivation_day not in WEEKDAYS:
        raise SystemExit(
            f"MOTIVATION_DAY должен быть одним из {', '.join(sorted(WEEKDAYS))}, "
            f"получено: {motivation_day!r}"
        )

    return Config(
        bot_token=token,
        group_chat_id=group_chat_id,
        admin_ids=_parse_ids(os.getenv("ADMIN_IDS", "")),
        timezone=timezone,
        poll_hour=_env_int("POLL_HOUR", 20, 0, 23),
        poll_minute=_env_int("POLL_MINUTE", 0, 0, 59),
        weekly_report_day=weekly_report_day,
        weekly_report_hour=_env_int("WEEKLY_REPORT_HOUR", 21, 0, 23),
        weekly_report_minute=_env_int("WEEKLY_REPORT_MINUTE", 0, 0, 59),
        motivation_day=motivation_day,
        motivation_hour=_env_int("MOTIVATION_HOUR", 9, 0, 23),
        motivation_minute=_env_int("MOTIVATION_MINUTE", 0, 0, 59),
        retention_days=_env_int("RETENTION_DAYS", 0, 0, 3650),
        db_path=os.getenv("DB_PATH", "activity.db").strip() or "activity.db",
    )
