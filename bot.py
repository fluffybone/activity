from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import date, datetime, time, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    ChatMemberUpdated,
    ErrorEvent,
    InputPollOption,
    InlineKeyboardMarkup,
    Message,
    PollAnswer,
    User,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from charts import MONTHS_GENITIVE, build_chart_html, period_label
from config import Config, load_config
from db import Database
from motivation import motivation_for_week

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
    BotCommand(command="activity", description="Активности (админ)"),
    BotCommand(command="motivation", description="Мотивация недели (админ)"),
    BotCommand(command="cancel", description="Отменить создание"),
    BotCommand(command="id", description="ID этого чата"),
    BotCommand(command="help", description="Справка"),
]

WEEKDAYS = [
    ("mon", "Пн"),
    ("tue", "Вт"),
    ("wed", "Ср"),
    ("thu", "Чт"),
    ("fri", "Пт"),
    ("sat", "Сб"),
    ("sun", "Вс"),
]
WEEKDAY_LABELS = dict(WEEKDAYS)


class ActivityForm(StatesGroup):
    title = State()
    description = State()
    schedule = State()
    custom_days = State()
    run_date = State()
    run_time = State()


def _today(config: Config) -> date:
    return datetime.now(config.timezone).date()


def _human_date(value: date) -> str:
    return f"{value.day} {MONTHS_GENITIVE[value.month - 1]} {value.year}"


def _motivation_text(config: Config) -> str:
    week = _today(config).isocalendar().week
    return f"<b>Мотивация недели</b>\n\n{motivation_for_week(week)}"


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
        "/poll — отправить опрос сейчас в эту группу\n"
        "/activity add — создать активность с расписанием\n"
        "/activity — список активностей группы\n"
        "/activity del ID — удалить активность\n"
        "/motivation — показать мотивацию недели\n"
        "/cancel — отменить создание активности\n\n"
        "<b>Только для владельца бота</b>\n"
        "/chats — список групп в рассылке\n"
        "/unregister — убрать эту группу из рассылки\n\n"
        f"Еженедельный отчёт: <b>{config.weekly_report_day}</b> в "
        f"<b>{config.weekly_report_hour:02d}:{config.weekly_report_minute:02d}</b> "
        f"({config.timezone})."
    )


def _not_command(message: Message) -> bool:
    return not (message.text or "").startswith("/")


def _parse_time_value(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", value)
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _schedule_label(days: str, run_date: str | None, run_time: str) -> str:
    if days == "daily":
        return f"ежедневно в {run_time}"
    if days == "once":
        return f"разово {run_date} в {run_time}"
    codes = [code for code in days.split(",") if code]
    if codes == ["mon", "tue", "wed", "thu", "fri"]:
        return f"будни в {run_time}"
    if len(codes) == 7:
        return f"ежедневно в {run_time}"
    labels = ", ".join(WEEKDAY_LABELS[code] for code in codes if code in WEEKDAY_LABELS)
    return f"{labels} в {run_time}"


def _schedule_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Будни", callback_data="act:sched:weekdays")
    builder.button(text="Ежедневно", callback_data="act:sched:daily")
    builder.button(text="Разово", callback_data="act:sched:once")
    builder.button(text="Выбрать дни", callback_data="act:sched:custom")
    builder.button(text="Отмена", callback_data="act:cancel")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def _weekdays_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    selected_set = set(selected)
    for code, label in WEEKDAYS:
        prefix = "✅ " if code in selected_set else ""
        builder.button(text=f"{prefix}{label}", callback_data=f"act:day:{code}")
    builder.button(text="Готово", callback_data="act:days:done")
    builder.button(text="Отмена", callback_data="act:cancel")
    builder.adjust(4, 3, 1, 1)
    return builder.as_markup()


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


ACTIVITY_DONE_TEXT = "Готово"
ACTIVITY_NOT_DONE_TEXT = "Не готово"
ACTIVITY_UNVOTE_NOTE = (
    "Отметьте «Готово», если выполнили. Повторное нажатие снимает отметку."
)


async def _post_activity_poll(
    bot: Bot,
    db: Database,
    activity: dict,
    run_date: date,
) -> None:
    description = (activity["description"] or "").strip()
    available = max(0, 250 - len(ACTIVITY_UNVOTE_NOTE) - 2)
    description = description[:available]
    poll_description = (
        f"{description}\n\n{ACTIVITY_UNVOTE_NOTE}" if description else ACTIVITY_UNVOTE_NOTE
    )
    message = await bot.send_poll(
        activity["chat_id"],
        question=f"Активность: {activity['title']}",
        options=[
            InputPollOption(text=ACTIVITY_DONE_TEXT),
            InputPollOption(text=ACTIVITY_NOT_DONE_TEXT),
        ],
        is_anonymous=False,
        allows_multiple_answers=True,
        allows_revoting=True,
        description=poll_description,
    )
    if message.poll is not None:
        await db.save_activity_poll(
            message.poll.id,
            activity["id"],
            activity["chat_id"],
            run_date,
        )


async def send_activity(
    bot: Bot,
    db: Database,
    config: Config,
    activity_id: int,
    scheduler: AsyncIOScheduler,
) -> None:
    activity = await db.get_activity(activity_id)
    if activity is None or not activity["active"]:
        return
    if not await db.is_chat_active(activity["chat_id"]):
        return

    run_date = _today(config)
    try:
        await _post_activity_poll(bot, db, activity, run_date)
    except Exception:
        logging.exception("Не удалось отправить активность %s", activity_id)
        return

    if activity["days"] == "once":
        await db.deactivate_activity(activity_id)
        _remove_activity_job(scheduler, activity_id)


def _add_activity_job(
    scheduler: AsyncIOScheduler,
    bot: Bot,
    db: Database,
    config: Config,
    activity: dict,
) -> None:
    job_id = f"activity:{activity['id']}"
    hour, minute = (int(part) for part in activity["run_time"].split(":"))
    if activity["days"] == "once":
        run_date = date.fromisoformat(activity["run_date"])
        trigger = DateTrigger(
            run_date=datetime.combine(
                run_date, time(hour, minute), tzinfo=config.timezone
            )
        )
    else:
        kwargs = {"hour": hour, "minute": minute, "timezone": config.timezone}
        if activity["days"] != "daily":
            kwargs["day_of_week"] = activity["days"]
        trigger = CronTrigger(**kwargs)
    scheduler.add_job(
        send_activity,
        trigger,
        args=[bot, db, config, activity["id"], scheduler],
        id=job_id,
        replace_existing=True,
        misfire_grace_time=3600,
    )


def _remove_activity_job(scheduler: AsyncIOScheduler, activity_id: int) -> None:
    try:
        scheduler.remove_job(f"activity:{activity_id}")
    except JobLookupError:
        pass


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


async def _send_activity_list(message: Message, db: Database) -> None:
    activities = await db.list_activities(message.chat.id)
    if not activities:
        await message.answer("Активностей пока нет. Создать: /activity add")
        return

    lines = ["<b>Активности этой группы</b>"]
    for activity in activities:
        schedule = _schedule_label(
            str(activity["days"]),
            activity["run_date"],
            str(activity["run_time"]),
        )
        lines.append(f"#{activity['id']} {html.escape(str(activity['title']))} — {schedule}")
    lines.append("\nУдалить: <code>/activity del ID</code>")
    await message.answer("\n".join(lines))


@router.message(Command("activity"))
async def cmd_activity(
    message: Message,
    command: CommandObject,
    bot: Bot,
    db: Database,
    config: Config,
    scheduler: AsyncIOScheduler,
    state: FSMContext,
) -> None:
    if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await message.reply("Активности создаются в группе.")
        return
    if not await _can_manage_options(message, bot, config):
        await message.reply(
            "Недостаточно прав. Команда доступна администраторам группы или владельцу бота."
        )
        return
    if message.from_user is None:
        return
    await _register_group(message, db)

    args = (command.args or "").split()
    action = args[0].lower() if args else ""
    if action in {"", "list"}:
        await _send_activity_list(message, db)
        return
    if action == "add":
        await state.clear()
        await state.set_state(ActivityForm.title)
        await state.update_data(
            owner_id=message.from_user.id,
            chat_id=message.chat.id,
        )
        await message.reply(
            "Введите название активности (до 100 символов), например «10 приседаний».\n"
            "/cancel — отмена."
        )
        return
    if action == "del":
        if len(args) < 2 or not args[1].isdigit():
            await message.reply("Использование: <code>/activity del ID</code>")
            return
        activity_id = int(args[1])
        if await db.deactivate_activity(activity_id, chat_id=message.chat.id):
            _remove_activity_job(scheduler, activity_id)
            await message.reply(f"Активность #{activity_id} удалена.")
        else:
            await message.reply("Активность с таким ID не найдена в этой группе.")
        return

    await message.reply(
        "Использование:\n"
        "/activity — список\n"
        "/activity add — создать\n"
        "/activity del ID — удалить"
    )


@router.message(Command("motivation"))
async def cmd_motivation(message: Message, bot: Bot, config: Config) -> None:
    if not await _can_manage_options(message, bot, config):
        await message.reply(
            "Недостаточно прав. Команда доступна администраторам группы или владельцу бота."
        )
        return
    await message.answer(_motivation_text(config))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if not data:
        await message.reply("Нечего отменять.")
        return
    await state.clear()
    await message.reply("Создание активности отменено.")


@router.message(ActivityForm.title, _not_command)
async def activity_title(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        await message.reply("Введите название текстом или /cancel.")
        return
    if len(text) > 100:
        await message.reply("Слишком длинное название. Максимум 100 символов.")
        return
    await state.update_data(title=text)
    await state.set_state(ActivityForm.description)
    await message.reply(
        "Теперь описание и задание (до 180 символов). Оно будет видно в опросе."
    )


@router.message(ActivityForm.description, _not_command)
async def activity_description(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        await message.reply("Введите описание текстом или /cancel.")
        return
    if len(text) > 180:
        await message.reply("Слишком длинное описание. Максимум 180 символов.")
        return
    await state.update_data(description=text)
    await state.set_state(ActivityForm.schedule)
    await message.reply(
        "Когда публиковать активность?",
        reply_markup=_schedule_keyboard(),
    )


async def _activity_callback_owner(
    callback: CallbackQuery, state: FSMContext
) -> dict | None:
    data = await state.get_data()
    if not data:
        await callback.answer(
            "Создание уже завершено или отменено.",
            show_alert=True,
        )
        return None
    if callback.from_user.id != data.get("owner_id"):
        await callback.answer(
            "Эту активность создаёт другой администратор.",
            show_alert=True,
        )
        return None
    return data


@router.callback_query(F.data.startswith("act:sched:"), ActivityForm.schedule)
async def activity_schedule(callback: CallbackQuery, state: FSMContext) -> None:
    if await _activity_callback_owner(callback, state) is None:
        return
    if callback.message is None:
        await callback.answer()
        return

    action = (callback.data or "").rsplit(":", 1)[-1]
    if action == "weekdays":
        await state.update_data(days="mon,tue,wed,thu,fri")
        await state.set_state(ActivityForm.run_time)
        await callback.message.edit_text(
            "Время публикации в формате ЧЧ:ММ (например, 15:45)."
        )
    elif action == "daily":
        await state.update_data(days="daily")
        await state.set_state(ActivityForm.run_time)
        await callback.message.edit_text(
            "Время публикации в формате ЧЧ:ММ (например, 15:45)."
        )
    elif action == "once":
        await state.update_data(days="once")
        await state.set_state(ActivityForm.run_date)
        await callback.message.edit_text(
            "Дата публикации в формате ГГГГ-ММ-ДД или ДД.ММ.ГГГГ."
        )
    elif action == "custom":
        await state.update_data(days="", selected_days=[])
        await state.set_state(ActivityForm.custom_days)
        await callback.message.edit_text(
            "Выберите дни недели и нажмите «Готово».",
            reply_markup=_weekdays_keyboard([]),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("act:day:"), ActivityForm.custom_days)
async def activity_toggle_day(callback: CallbackQuery, state: FSMContext) -> None:
    if await _activity_callback_owner(callback, state) is None:
        return
    if callback.message is None:
        await callback.answer()
        return

    day = (callback.data or "").rsplit(":", 1)[-1]
    data = await state.get_data()
    selected = set(data.get("selected_days", []))
    if day in selected:
        selected.remove(day)
    elif day in WEEKDAY_LABELS:
        selected.add(day)
    ordered = [code for code, _ in WEEKDAYS if code in selected]
    await state.update_data(selected_days=ordered)
    await callback.message.edit_reply_markup(reply_markup=_weekdays_keyboard(ordered))
    await callback.answer()


@router.callback_query(F.data == "act:days:done", ActivityForm.custom_days)
async def activity_days_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await _activity_callback_owner(callback, state)
    if data is None:
        return
    selected = data.get("selected_days", [])
    if not selected:
        await callback.answer("Выберите хотя бы один день.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer()
        return

    await state.update_data(days=",".join(selected))
    await state.set_state(ActivityForm.run_time)
    await callback.message.edit_text(
        "Время публикации в формате ЧЧ:ММ (например, 15:45)."
    )
    await callback.answer()


@router.callback_query(F.data == "act:cancel")
async def activity_cancel_callback(
    callback: CallbackQuery, state: FSMContext
) -> None:
    if await _activity_callback_owner(callback, state) is None:
        return
    if callback.message is not None:
        await callback.message.edit_text("Создание активности отменено.")
    await state.clear()
    await callback.answer()


@router.message(ActivityForm.run_date, _not_command)
async def activity_run_date(
    message: Message, state: FSMContext, config: Config
) -> None:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        await message.reply("Введите дату текстом или /cancel.")
        return
    parsed = _parse_date(text)
    if parsed is None:
        await message.reply("Не поняла дату. Формат: 2026-10-25 или 25.10.2026.")
        return
    if parsed < _today(config):
        await message.reply("Дата уже прошла. Укажите будущую дату.")
        return
    await state.update_data(run_date=parsed.isoformat())
    await state.set_state(ActivityForm.run_time)
    await message.reply("Время публикации в формате ЧЧ:ММ (например, 15:45).")


@router.message(ActivityForm.run_time, _not_command)
async def activity_run_time(
    message: Message,
    state: FSMContext,
    bot: Bot,
    db: Database,
    config: Config,
    scheduler: AsyncIOScheduler,
) -> None:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        await message.reply("Введите время текстом или /cancel.")
        return
    parsed = _parse_time_value(text)
    if parsed is None:
        await message.reply("Не поняла время. Формат: 15:45.")
        return

    data = await state.get_data()
    if (
        not data
        or "chat_id" not in data
        or "title" not in data
        or "description" not in data
        or not data.get("days")
    ):
        await state.clear()
        await message.reply("Не удалось восстановить данные. Начните заново: /activity add")
        return

    hour, minute = parsed
    run_time = f"{hour:02d}:{minute:02d}"
    days = str(data["days"])
    if days == "once":
        run_date = date.fromisoformat(str(data["run_date"]))
        run_at = datetime.combine(
            run_date, time(hour, minute), tzinfo=config.timezone
        )
        if run_at <= datetime.now(config.timezone):
            await message.reply("Это время уже прошло. Укажите другое.")
            return

    activity_id = await db.add_activity(
        chat_id=int(data["chat_id"]),
        title=str(data["title"]),
        description=str(data["description"]),
        days=days,
        run_date=data.get("run_date"),
        run_time=run_time,
    )
    activity = await db.get_activity(activity_id)
    if activity is not None:
        _add_activity_job(scheduler, bot, db, config, activity)

    await state.clear()
    schedule = _schedule_label(days, data.get("run_date"), run_time)
    await message.reply(
        "✅ Активность создана.\n\n"
        f"<b>{html.escape(str(data['title']))}</b>\n"
        f"{html.escape(str(data['description']))}\n\n"
        f"Когда: {schedule}\n"
        f"ID: <code>{activity_id}</code>"
    )


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

    activity_poll = await db.get_activity_poll(answer.poll_id)
    if activity_poll is not None:
        if not answer.option_ids:
            await db.delete_activity_answer(
                activity_poll["activity_id"],
                activity_poll["run_date"],
                activity_poll["chat_id"],
                answer.user.id,
            )
        else:
            await db.set_activity_answer(
                activity_poll["activity_id"],
                activity_poll["run_date"],
                activity_poll["chat_id"],
                answer.user.id,
                _display_name(answer.user),
                0 in answer.option_ids,
            )
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


async def send_motivation(bot: Bot, db: Database, config: Config) -> None:
    text = _motivation_text(config)
    for chat_id in await _active_chat_ids(db, config):
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            logging.exception("Не удалось отправить мотивацию в чат %s", chat_id)


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

    dispatcher = Dispatcher(storage=MemoryStorage())
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
    scheduler.add_job(
        send_motivation,
        CronTrigger(
            day_of_week=config.motivation_day,
            hour=config.motivation_hour,
            minute=config.motivation_minute,
            timezone=config.timezone,
        ),
        args=[bot, db, config],
        id="weekly_motivation",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    if config.retention_days > 0:
        scheduler.add_job(
            cleanup_old_answers,
            CronTrigger(hour=4, minute=0, timezone=config.timezone),
            args=[bot, db, config],
            id="cleanup_old_answers",
            replace_existing=True,
        )

    activity_count = 0
    for activity in await db.list_active_activities():
        if activity["days"] == "once":
            run_date = date.fromisoformat(str(activity["run_date"]))
            hour, minute = (int(part) for part in str(activity["run_time"]).split(":"))
            run_at = datetime.combine(
                run_date, time(hour, minute), tzinfo=config.timezone
            )
            if run_at <= datetime.now(config.timezone):
                await db.deactivate_activity(int(activity["id"]))
                continue
        _add_activity_job(scheduler, bot, db, config, activity)
        activity_count += 1

    scheduler.start()

    logging.info(
        "Бот запущен. Часовой пояс: %s, БД: %s, групп в рассылке: %d",
        config.timezone,
        config.db_path,
        len(await db.list_chats()),
    )
    logging.info(
        "Ежедневный опрос: %02d:%02d. Еженедельный отчёт: %s в %02d:%02d. "
        "Мотивация: %s в %02d:%02d. Активностей в расписании: %d.",
        config.poll_hour,
        config.poll_minute,
        config.weekly_report_day,
        config.weekly_report_hour,
        config.weekly_report_minute,
        config.motivation_day,
        config.motivation_hour,
        config.motivation_minute,
        activity_count,
    )
    if config.retention_days > 0:
        logging.info("Автоочистка ответов старше %d дней включена.", config.retention_days)

    try:
        await dispatcher.start_polling(bot, db=db, config=config, scheduler=scheduler)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
