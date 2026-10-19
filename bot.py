from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import date, datetime, timedelta

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    ChatMemberUpdated,
    ErrorEvent,
    InputPollOption,
    Message,
    PollAnswer,
    User,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from charts import MONTHS_GENITIVE, build_chart_html, period_label
from config import Config, load_config
from db import Database

router = Router()

COMMANDS = [
    BotCommand(command="today", description="График за сегодня"),
    BotCommand(command="week", description="График за неделю"),
    BotCommand(command="month", description="График за месяц"),
    BotCommand(command="year", description="График за год"),
    BotCommand(command="chart", description="График за произвольный период"),
    BotCommand(command="my", description="Мой выбор за сегодня"),
    BotCommand(command="delete_me", description="Удалить мои отметки"),
    BotCommand(command="options", description="Варианты опроса"),
    BotCommand(command="addoption", description="Добавить вариант (админ)"),
    BotCommand(command="deloption", description="Скрыть вариант (админ)"),
    BotCommand(command="poll", description="Отправить опрос сейчас (админ)"),
    BotCommand(command="chats", description="Группы в рассылке (админ)"),
    BotCommand(command="unregister", description="Убрать группу из рассылки (админ)"),
    BotCommand(command="id", description="ID этого чата"),
    BotCommand(command="help", description="Справка"),
]


def _today(config: Config) -> date:
    return datetime.now(config.timezone).date()


def _human_date(value: date) -> str:
    return f"{value.day} {MONTHS_GENITIVE[value.month - 1]} {value.year}"


def _display_name(user: User) -> str:
    first_name = (user.first_name or "").strip()
    if first_name:
        return first_name
    return f"ID {user.id}"


def _parse_date(value: str) -> date | None:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _week_bounds(today: date) -> tuple[date, date]:
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


def _month_bounds(today: date) -> tuple[date, date]:
    start = today.replace(day=1)
    next_month = (start + timedelta(days=32)).replace(day=1)
    return start, next_month - timedelta(days=1)


def _help_text(config: Config) -> str:
    return (
        "👋 <b>Бот ежедневной активности</b>\n\n"
        f"Каждый день в <b>{config.poll_hour:02d}:{config.poll_minute:02d}</b> "
        "я присылаю опрос. Отметьте один или несколько вариантов — голос "
        "можно изменить, а выбор участников виден всей группе.\n\n"
        "<b>Графики</b> (приходят HTML-файлом — откройте в браузере)\n"
        "/today — за сегодня\n"
        "/week — за текущую неделю\n"
        "/month — за текущий месяц\n"
        "/year — за текущий год (по месяцам)\n"
        "/chart 2026-10-01 2026-10-31 — за произвольный период\n"
        "/my — ваш выбор за сегодня\n"
        "/delete_me — удалить все свои отметки\n"
        "/options — список вариантов этой группы\n"
        "/id — ID этого чата\n\n"
        "У каждой группы свои варианты и свой график. Опрос и отчёт бот присылает\n"
        "во все группы, куда добавлен. Чтобы подключить новую — добавьте туда бота.\n\n"
        "<b>Для администраторов группы</b>\n"
        "/addoption Название [цвет] — добавить вариант в эту группу\n"
        "/deloption ID или название — скрыть вариант в этой группе\n"
        "/poll — отправить опрос сейчас в эту группу\n\n"
        "<b>Только для владельца бота</b>\n"
        "/chats — список групп в рассылке\n"
        "/unregister — убрать эту группу из рассылки\n\n"
        f"Еженедельный отчёт: <b>{config.weekly_report_day}</b> в "
        f"<b>{config.weekly_report_hour:02d}:{config.weekly_report_minute:02d}</b> "
        f"({config.timezone})."
    )


def _is_owner(message: Message, config: Config) -> bool:
    return message.from_user is not None and message.from_user.id in config.admin_ids


def _owner_only_text(config: Config) -> str:
    if not config.admin_ids:
        return (
            "Команда доступна только владельцу бота. Укажите свой Telegram ID "
            "в <code>ADMIN_IDS</code> в .env и перезапустите бота."
        )
    return "Недостаточно прав. Команда доступна только владельцу бота."


async def _can_manage_options(message: Message, bot: Bot, config: Config) -> bool:
    if message.from_user is None:
        return False
    if message.from_user.id in config.admin_ids:
        return True
    if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        try:
            member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        except TelegramBadRequest:
            return False
        return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}
    return False


async def _register_group(message: Message, db: Database) -> None:
    if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await db.add_chat(message.chat.id, message.chat.title or "")


async def _active_chat_ids(
    db: Database,
    config: Config,
    *,
    current_chat_id: int | None = None,
) -> list[int]:
    if current_chat_id is not None:
        return [current_chat_id]
    chat_ids = [chat["chat_id"] for chat in await db.list_chats()]
    if config.group_chat_id is not None and config.group_chat_id not in chat_ids:
        chat_ids.append(config.group_chat_id)
    return chat_ids


async def _post_poll_to_chat(
    bot: Bot,
    db: Database,
    chat_id: int,
    poll_date: date,
) -> Message | None:
    options = await db.list_options(chat_id, active_only=True)

    if not options:
        await bot.send_message(
            chat_id,
            "⚠️ Варианты опроса не настроены.\n"
            "Администратор может добавить их командой:\n"
            "<code>/addoption Название</code>",
        )
        return None
    if len(options) > 12:
        await bot.send_message(
            chat_id,
            "⚠️ В нативном опросе Telegram может быть не больше 12 вариантов.\n"
            "Скройте лишние через /deloption.",
        )
        return None

    message = await bot.send_poll(
        chat_id,
        question=f"Опрос на {_human_date(poll_date)}",
        options=[
            InputPollOption(text=str(option["label"]))
            for option in options
        ],
        is_anonymous=False,
        allows_multiple_answers=True,
        allows_revoting=True,
        description=(
            "Отметьте всё, что подходит. Можно выбрать несколько вариантов. "
            "Голоса видны участникам группы."
        ),
    )
    if message.poll is not None:
        await db.save_poll(
            message.poll.id,
            chat_id,
            poll_date,
            [int(option["id"]) for option in options],
        )
    return message


async def post_poll(
    bot: Bot,
    db: Database,
    config: Config,
    *,
    poll_date: date | None = None,
    current_chat_id: int | None = None,
) -> int:
    poll_date = poll_date or _today(config)
    sent = 0
    for chat_id in await _active_chat_ids(db, config, current_chat_id=current_chat_id):
        try:
            message = await _post_poll_to_chat(bot, db, chat_id, poll_date)
        except Exception:
            logging.exception("Не удалось отправить опрос в чат %s", chat_id)
            continue
        if message is not None:
            sent += 1
    return sent


async def _send_chart(
    bot: Bot,
    db: Database,
    chat_id: int,
    start: date,
    end: date,
    mode: str,
) -> None:
    options = await db.list_options(chat_id, active_only=False)
    answers = await db.get_answers(chat_id, start, end)
    html_doc = build_chart_html(
        answers=answers,
        options=options,
        start=start,
        end=end,
        mode=mode,
    )
    filename = f"chart_{start.isoformat()}_{end.isoformat()}.html"
    await bot.send_document(
        chat_id,
        BufferedInputFile(html_doc.encode("utf-8"), filename=filename),
        caption=(
            f"📊 <b>Активность</b>\n{period_label(start, end)}\n"
            "Откройте файл в браузере."
        ),
    )


@router.message(Command("start", "help"))
async def cmd_help(message: Message, db: Database, config: Config) -> None:
    await _register_group(message, db)
    await message.answer(_help_text(config))


@router.message(Command("id", "chatid"))
async def cmd_id(message: Message) -> None:
    await message.reply(
        f"Chat ID: <code>{message.chat.id}</code>\n"
        f"Тип чата: <code>{message.chat.type}</code>"
    )


@router.message(Command("chats"))
async def cmd_chats(message: Message, bot: Bot, db: Database, config: Config) -> None:
    if not _is_owner(message, config):
        await message.reply(_owner_only_text(config))
        return

    chats = await db.list_chats()
    if not chats:
        await message.reply("Нет групп в рассылке. Добавьте бота в группу — она подключится автоматически.")
        return

    lines = ["<b>Группы в рассылке</b>", ""]
    for chat in chats:
        title = html.escape(str(chat["title"] or "Без названия"))
        lines.append(f"• {title} — <code>{chat['chat_id']}</code>")
    await message.reply("\n".join(lines))


@router.message(Command("unregister"))
async def cmd_unregister(message: Message, bot: Bot, db: Database, config: Config) -> None:
    if not _is_owner(message, config):
        await message.reply(_owner_only_text(config))
        return

    removed = await db.deactivate_chat(message.chat.id)
    if removed:
        await message.reply("Эта группа убрана из рассылки. Чтобы вернуть — добавьте бота заново.")
    else:
        await message.reply("Эта группа и так не участвовала в рассылке.")


@router.my_chat_member()
async def on_my_chat_member(
    event: ChatMemberUpdated,
    bot: Bot,
    db: Database,
    config: Config,
) -> None:
    chat = event.chat
    if chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return

    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status
    inside = {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    }
    outside = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}

    if new_status in outside:
        await db.deactivate_chat(chat.id)
        return

    if new_status not in inside:
        return

    await db.add_chat(chat.id, chat.title or "")

    if old_status in outside:
        text = (
            f"👋 Бот подключён к группе «{html.escape(chat.title or '')}».\n"
            f"Chat ID: <code>{chat.id}</code>\n\n"
            "Опрос будет приходить ежедневно, а отчёт — в конце недели. "
            "Варианты настраиваются командой /addoption."
        )
        try:
            await bot.send_message(chat.id, text)
        except TelegramBadRequest as exc:
            logging.warning("Не удалось поприветствовать чат %s: %s", chat.id, exc)

        for admin_id in config.admin_ids:
            try:
                await bot.send_message(admin_id, text)
            except TelegramBadRequest as exc:
                logging.warning("Не удалось уведомить админа %s: %s", admin_id, exc)


@router.message(Command("options"))
async def cmd_options(message: Message, db: Database) -> None:
    await _register_group(message, db)
    options = await db.list_options(message.chat.id, active_only=True)
    if not options:
        await message.reply(
            "Пока нет активных вариантов.\n"
            "Администратор может добавить: <code>/addoption Название</code>"
        )
        return

    lines = ["<b>Варианты опроса</b>", ""]
    for option in options:
        lines.append(
            f"• {html.escape(str(option['label']))} <code>#{option['id']}</code>"
        )
    await message.reply("\n".join(lines))


@router.message(Command("addoption"))
async def cmd_addoption(
    message: Message,
    command: CommandObject,
    bot: Bot,
    db: Database,
    config: Config,
) -> None:
    if not await _can_manage_options(message, bot, config):
        await message.reply("Недостаточно прав. Команда доступна администраторам группы или владельцу бота.")
        return

    raw = (command.args or "").strip()
    if not raw:
        await message.reply(
            "Использование: <code>/addoption Название</code>\n"
            "Можно указать цвет: <code>/addoption Спорт #3498DB</code>"
        )
        return

    label = raw
    color = None
    parts = raw.rsplit(None, 1)
    if len(parts) == 2 and re.fullmatch(r"#?[0-9a-fA-F]{6}", parts[1]):
        label = parts[0].strip()
        color = "#" + parts[1].lstrip("#").upper()

    if not label:
        await message.reply("Название варианта не может быть пустым.")
        return
    if len(label) > 48:
        await message.reply("Название слишком длинное. Максимум 48 символов.")
        return

    await _register_group(message, db)
    active_options = await db.list_options(message.chat.id, active_only=True)
    existing = next(
        (
            option
            for option in await db.list_options(message.chat.id, active_only=False)
            if str(option["label"]).casefold() == label.casefold()
        ),
        None,
    )
    if len(active_options) >= 12 and not (existing and existing["active"]):
        await message.reply(
            "В нативном опросе Telegram может быть не больше 12 вариантов. "
            "Сначала скройте лишние через /deloption."
        )
        return

    result = await db.add_option(message.chat.id, label, color)

    if result["created"]:
        text = (
            f"Добавлен вариант «{html.escape(result['label'])}» "
            f"(ID <code>{result['id']}</code>, цвет <code>{result['color']}</code>)."
        )
    elif result["reactivated"]:
        text = (
            f"Вариант «{html.escape(result['label'])}» снова активен "
            f"(ID <code>{result['id']}</code>)."
        )
    elif result.get("updated"):
        text = (
            f"Цвет варианта «{html.escape(result['label'])}» обновлён на "
            f"<code>{result['color']}</code>."
        )
    else:
        text = (
            f"Такой вариант уже есть: «{html.escape(result['label'])}» "
            f"(ID <code>{result['id']}</code>)."
        )
    await message.reply(text)


@router.message(Command("deloption"))
async def cmd_deloption(
    message: Message,
    command: CommandObject,
    bot: Bot,
    db: Database,
    config: Config,
) -> None:
    if not await _can_manage_options(message, bot, config):
        await message.reply("Недостаточно прав. Команда доступна администраторам группы или владельцу бота.")
        return

    identifier = (command.args or "").strip()
    if not identifier:
        await message.reply("Использование: <code>/deloption ID</code> или <code>/deloption Название</code>")
        return

    await _register_group(message, db)
    result = await db.deactivate_option(message.chat.id, identifier)
    if result is None:
        await message.reply("Вариант не найден. Посмотрите список: /options")
        return
    if result["already_inactive"]:
        await message.reply(f"Вариант «{html.escape(str(result['label']))}» уже скрыт.")
        return

    await message.reply(
        f"Вариант «{html.escape(str(result['label']))}» скрыт. "
        "История ответов сохранена и останется на графиках."
    )


@router.message(Command("poll"))
async def cmd_poll(
    message: Message,
    bot: Bot,
    db: Database,
    config: Config,
) -> None:
    if not await _can_manage_options(message, bot, config):
        await message.reply("Недостаточно прав. Команда доступна администраторам группы или владельцу бота.")
        return

    current_chat_id: int | None = None
    if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await _register_group(message, db)
        current_chat_id = message.chat.id

    sent = await post_poll(bot, db, config, current_chat_id=current_chat_id)
    if sent == 0:
        await message.reply("Опрос не отправлен: нет групп в рассылке или не настроены варианты (/addoption).")


@router.message(Command("my"))
async def cmd_my(
    message: Message,
    command: CommandObject,
    db: Database,
    config: Config,
) -> None:
    if message.from_user is None:
        return

    target = _today(config)
    if (command.args or "").strip():
        parsed = _parse_date(command.args or "")
        if parsed is None:
            await message.reply(
                "Не понял дату. Пример: <code>/my 2026-10-19</code> "
                "или <code>/my 19.10.2026</code>"
            )
            return
        target = parsed

    option_ids = await db.get_user_answers(message.chat.id, target, message.from_user.id)
    if not option_ids:
        await message.reply(f"За {_human_date(target)} у вас пока нет отметок.")
        return

    all_options = {
        int(option["id"]): option
        for option in await db.list_options(message.chat.id, active_only=False)
    }
    lines = [f"<b>Ваш выбор за {_human_date(target)}</b>", ""]
    for option_id in option_ids:
        option = all_options.get(option_id)
        if option is None:
            continue
        lines.append(f"• {html.escape(str(option['label']))}")
    await message.reply("\n".join(lines))


@router.message(Command("delete_me"))
async def cmd_delete_me(message: Message, db: Database) -> None:
    if message.from_user is None:
        return
    deleted = await db.delete_user_answers(message.from_user.id)
    if deleted:
        await message.reply(
            f"Удалено отметок: {deleted} (во всех группах). Имя и история ответов стёрты."
        )
    else:
        await message.reply("У вас нет сохранённых отметок.")


@router.message(Command("today"))
async def cmd_today(message: Message, bot: Bot, db: Database, config: Config) -> None:
    today = _today(config)
    await _send_chart(bot, db, message.chat.id, today, today, "daily")


@router.message(Command("week"))
async def cmd_week(message: Message, bot: Bot, db: Database, config: Config) -> None:
    start, end = _week_bounds(_today(config))
    await _send_chart(bot, db, message.chat.id, start, end, "daily")


@router.message(Command("month"))
async def cmd_month(message: Message, bot: Bot, db: Database, config: Config) -> None:
    start, end = _month_bounds(_today(config))
    await _send_chart(bot, db, message.chat.id, start, end, "daily")


@router.message(Command("year"))
async def cmd_year(message: Message, bot: Bot, db: Database, config: Config) -> None:
    today = _today(config)
    await _send_chart(
        bot,
        db,
        message.chat.id,
        date(today.year, 1, 1),
        date(today.year, 12, 31),
        "monthly",
    )


@router.message(Command("chart"))
async def cmd_chart(
    message: Message,
    command: CommandObject,
    bot: Bot,
    db: Database,
    config: Config,
) -> None:
    today = _today(config)
    raw = (command.args or "").strip()
    normalized = raw.lower()

    if not raw or normalized in {"week", "неделя"}:
        start, end, mode = *_week_bounds(today), "daily"
    elif normalized in {"today", "сегодня"}:
        start, end, mode = today, today, "daily"
    elif normalized in {"month", "месяц"}:
        start, end, mode = *_month_bounds(today), "daily"
    elif normalized in {"year", "год"}:
        start, end, mode = date(today.year, 1, 1), date(today.year, 12, 31), "monthly"
    else:
        parts = re.split(r"\s+|\.\.", raw)
        if len(parts) != 2:
            await message.reply(
                "Использование:\n"
                "<code>/chart 2026-10-01 2026-10-31</code>\n"
                "или <code>/chart week</code>, <code>/chart month</code>, <code>/chart year</code>"
            )
            return
        start = _parse_date(parts[0])
        end = _parse_date(parts[1])
        if start is None or end is None:
            await message.reply(
                "Не понял даты. Используйте формат ГГГГ-ММ-ДД или ДД.ММ.ГГГГ."
            )
            return
        if end < start:
            await message.reply("Конец периода не может быть раньше начала.")
            return
        if (end - start).days > 365 * 5:
            await message.reply("Слишком большой период. Максимум 5 лет.")
            return
        mode = "daily" if (end - start).days <= 34 else "monthly"

    await _send_chart(bot, db, message.chat.id, start, end, mode)


@router.poll_answer()
async def on_poll_answer(answer: PollAnswer, db: Database) -> None:
    if answer.user is None:
        return

    poll = await db.get_poll(answer.poll_id)
    if poll is None:
        return

    mapping: list[int] = poll["option_ids"]
    chosen = [
        mapping[index]
        for index in answer.option_ids
        if 0 <= index < len(mapping)
    ]

    await db.replace_user_answers(
        poll["chat_id"],
        poll["poll_date"],
        answer.user.id,
        _display_name(answer.user),
        chosen,
    )


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    logging.error("Ошибка при обработке обновления", exc_info=event.exception)
    return True


async def send_daily_poll(bot: Bot, db: Database, config: Config) -> None:
    try:
        await post_poll(bot, db, config)
    except Exception:
        logging.exception("Не удалось отправить ежедневный опрос")


async def send_weekly_report(bot: Bot, db: Database, config: Config) -> None:
    start, end = _week_bounds(_today(config))
    for chat_id in await _active_chat_ids(db, config):
        try:
            await _send_chart(bot, db, chat_id, start, end, "daily")
        except Exception:
            logging.exception("Не удалось отправить еженедельный отчёт в чат %s", chat_id)


async def cleanup_old_answers(bot: Bot, db: Database, config: Config) -> None:
    if config.retention_days <= 0:
        return
    cutoff = _today(config) - timedelta(days=config.retention_days)
    try:
        deleted = await db.delete_answers_before(cutoff)
    except Exception:
        logging.exception("Не удалось очистить старые ответы")
        return
    if deleted:
        logging.info(
            "Удалено ответов старше %s: %d", cutoff.isoformat(), deleted
        )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    config = load_config()
    db = Database(config.db_path)
    await db.init()

    bot = Bot(
        config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await bot.set_my_commands(COMMANDS)

    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except TelegramBadRequest as exc:
        logging.warning("Не удалось удалить webhook: %s", exc)

    if config.group_chat_id is not None:
        try:
            chat = await bot.get_chat(config.group_chat_id)
            await db.add_chat(config.group_chat_id, chat.title or "")
        except TelegramBadRequest as exc:
            logging.warning(
                "Не удалось зарегистрировать GROUP_CHAT_ID %s: %s",
                config.group_chat_id,
                exc,
            )

    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    scheduler = AsyncIOScheduler(timezone=config.timezone)
    scheduler.add_job(
        send_daily_poll,
        CronTrigger(
            hour=config.poll_hour,
            minute=config.poll_minute,
            timezone=config.timezone,
        ),
        args=[bot, db, config],
        id="daily_poll",
        replace_existing=True,
    )
    scheduler.add_job(
        send_weekly_report,
        CronTrigger(
            day_of_week=config.weekly_report_day,
            hour=config.weekly_report_hour,
            minute=config.weekly_report_minute,
            timezone=config.timezone,
        ),
        args=[bot, db, config],
        id="weekly_report",
        replace_existing=True,
    )
    if config.retention_days > 0:
        scheduler.add_job(
            cleanup_old_answers,
            CronTrigger(hour=4, minute=0, timezone=config.timezone),
            args=[bot, db, config],
            id="cleanup_old_answers",
            replace_existing=True,
        )
    scheduler.start()

    logging.info(
        "Бот запущен. Часовой пояс: %s, БД: %s, групп в рассылке: %d",
        config.timezone,
        config.db_path,
        len(await db.list_chats()),
    )
    logging.info(
        "Ежедневный опрос: %02d:%02d. Еженедельный отчёт: %s в %02d:%02d.",
        config.poll_hour,
        config.poll_minute,
        config.weekly_report_day,
        config.weekly_report_hour,
        config.weekly_report_minute,
    )
    if config.retention_days > 0:
        logging.info("Автоочистка ответов старше %d дней включена.", config.retention_days)

    try:
        await dispatcher.start_polling(bot, db=db, config=config)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
