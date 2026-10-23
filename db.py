from __future__ import annotations

import colorsys
import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Any, AsyncIterator

import aiosqlite

PALETTE: list[str] = [
    "#3498DB",
    "#2ECC71",
    "#F1C40F",
    "#9B59B6",
    "#E67E22",
    "#1ABC9C",
    "#00BCD4",
    "#E91E63",
    "#7F8C8D",
    "#BDC3C7",
]

DEFAULT_OPTIONS = ["Тренировка", "Шаги", "Танцы", "Падел", "Отдых", "На ленивом"]

DEFAULT_COLORS: dict[str, str] = {
    "Отдых": "#E3F2FD",
    "На ленивом": "#FCE4EC",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL DEFAULT 0,
    label TEXT NOT NULL,
    color TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS answers (
    poll_date TEXT NOT NULL,
    chat_id INTEGER NOT NULL DEFAULT 0,
    user_id INTEGER NOT NULL,
    user_name TEXT NOT NULL,
    option_id INTEGER NOT NULL,
    PRIMARY KEY (poll_date, chat_id, user_id, option_id)
);

CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS polls (
    poll_id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    poll_date TEXT NOT NULL,
    option_ids TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    days TEXT NOT NULL,
    run_date TEXT,
    run_time TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_polls (
    poll_id TEXT PRIMARY KEY,
    activity_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    run_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_answers (
    activity_id INTEGER NOT NULL,
    run_date TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    user_name TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (activity_id, run_date, chat_id, user_id)
);
"""


def _generated_color(index: int) -> str:
    hue = (index * 0.381966 + 0.08) % 1.0
    red, green, blue = colorsys.hls_to_rgb(hue, 0.52, 0.62)
    return f"#{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}"


def _next_color(existing: list[str]) -> str:
    used = {color.upper() for color in existing}
    for color in PALETTE:
        if color.upper() not in used:
            return color
    return _generated_color(len(used))


class Database:
    def __init__(self, path: str) -> None:
        self.path = path

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout = 5000")
            yield db

    async def init(self) -> None:
        async with self._connect() as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.executescript(SCHEMA)
            await self._ensure_indexes(db)
            await db.commit()

    async def _ensure_indexes(self, db: aiosqlite.Connection) -> None:
        await db.execute("CREATE INDEX IF NOT EXISTS idx_options_chat ON options (chat_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_answers_date ON answers (poll_date)")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_answers_chat_date ON answers (chat_id, poll_date)"
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_polls_date ON polls (poll_date)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_activities_chat ON activities (chat_id)")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_answers_date ON activity_answers (run_date)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_polls_date ON activity_polls (run_date)"
        )

    async def _chat_option_count(self, db: aiosqlite.Connection, chat_id: int) -> int:
        cursor = await db.execute(
            "SELECT COUNT(*) AS count FROM options WHERE chat_id = ?", (chat_id,)
        )
        return (await cursor.fetchone())["count"]

    async def _seed_default_options(self, db: aiosqlite.Connection, chat_id: int) -> None:
        used_colors: list[str] = []
        for position, label in enumerate(DEFAULT_OPTIONS):
            color = DEFAULT_COLORS.get(label)
            if color is None:
                color = _next_color(used_colors)
            used_colors.append(color)
            await db.execute(
                "INSERT INTO options (chat_id, label, color, position, active) VALUES (?, ?, ?, ?, 1)",
                (chat_id, label, color, position),
            )

    async def _ensure_chat_options(self, db: aiosqlite.Connection, chat_id: int) -> None:
        if await self._chat_option_count(db, chat_id):
            return
        await self._seed_default_options(db, chat_id)

    async def list_options(self, chat_id: int, active_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT id, label, color, position, active FROM options WHERE chat_id = ?"
        if active_only:
            query += " AND active = 1"
        query += " ORDER BY position, id"
        async with self._connect() as db:
            cursor = await db.execute(query, (chat_id,))
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def add_option(
        self, chat_id: int, label: str, color: str | None = None
    ) -> dict[str, Any]:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT id, label, color, active FROM options WHERE chat_id = ?", (chat_id,)
            )
            rows = [dict(row) for row in await cursor.fetchall()]
            existing = next(
                (row for row in rows if row["label"].casefold() == label.casefold()),
                None,
            )

            if existing:
                if existing["active"]:
                    new_color = existing["color"]
                    updated = False
                    if color is not None and color.upper() != existing["color"].upper():
                        new_color = color
                        updated = True
                        await db.execute(
                            "UPDATE options SET color = ? WHERE id = ?",
                            (new_color, existing["id"]),
                        )
                        await db.commit()
                    return {
                        "id": existing["id"],
                        "label": existing["label"],
                        "color": new_color,
                        "created": False,
                        "reactivated": False,
                        "updated": updated,
                    }
                new_color = color or existing["color"]
                await db.execute(
                    "UPDATE options SET active = 1, color = ? WHERE id = ?",
                    (new_color, existing["id"]),
                )
                await db.commit()
                return {
                    "id": existing["id"],
                    "label": existing["label"],
                    "color": new_color,
                    "created": False,
                    "reactivated": True,
                    "updated": False,
                }

            if color is None:
                color = _next_color([row["color"] for row in rows])

            cursor = await db.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 AS pos FROM options WHERE chat_id = ?",
                (chat_id,),
            )
            position = (await cursor.fetchone())["pos"]
            cursor = await db.execute(
                "INSERT INTO options (chat_id, label, color, position, active) VALUES (?, ?, ?, ?, 1)",
                (chat_id, label, color, position),
            )
            await db.commit()
            return {
                "id": cursor.lastrowid,
                "label": label,
                "color": color,
                "created": True,
                "reactivated": False,
                "updated": False,
            }

    async def deactivate_option(self, chat_id: int, identifier: str) -> dict[str, Any] | None:
        identifier = identifier.strip()
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT id, label, color, active FROM options WHERE chat_id = ?", (chat_id,)
            )
            rows = [dict(row) for row in await cursor.fetchall()]

            if identifier.isdigit():
                target = next((row for row in rows if row["id"] == int(identifier)), None)
            else:
                target = next(
                    (row for row in rows if row["label"].casefold() == identifier.casefold()),
                    None,
                )

            if target is None:
                return None
            if not target["active"]:
                return {**target, "already_inactive": True}

            await db.execute("UPDATE options SET active = 0 WHERE id = ?", (target["id"],))
            await db.commit()
            return {**target, "already_inactive": False}

    async def get_user_answers(
        self, chat_id: int, poll_date: date, user_id: int
    ) -> list[int]:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT option_id FROM answers WHERE poll_date = ? AND chat_id = ? AND user_id = ? ORDER BY option_id",
                (poll_date.isoformat(), chat_id, user_id),
            )
            rows = await cursor.fetchall()
        return [row["option_id"] for row in rows]

    async def get_answers(
        self, chat_id: int, start: date, end: date
    ) -> list[dict[str, Any]]:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT poll_date, user_id, user_name, option_id
                FROM answers
                WHERE chat_id = ? AND poll_date BETWEEN ? AND ?
                ORDER BY poll_date, user_id
                """,
                (chat_id, start.isoformat(), end.isoformat()),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def save_poll(
        self, poll_id: str, chat_id: int, poll_date: date, option_ids: list[int]
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO polls (poll_id, chat_id, poll_date, option_ids) VALUES (?, ?, ?, ?)
                ON CONFLICT(poll_id) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    poll_date = excluded.poll_date,
                    option_ids = excluded.option_ids
                """,
                (poll_id, chat_id, poll_date.isoformat(), json.dumps(option_ids)),
            )
            await db.commit()

    async def get_poll(self, poll_id: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT poll_id, chat_id, poll_date, option_ids FROM polls WHERE poll_id = ?",
                (poll_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "poll_id": row["poll_id"],
            "chat_id": row["chat_id"],
            "poll_date": date.fromisoformat(row["poll_date"]),
            "option_ids": json.loads(row["option_ids"]),
        }

    async def replace_user_answers(
        self,
        chat_id: int,
        poll_date: date,
        user_id: int,
        user_name: str,
        option_ids: list[int],
    ) -> None:
        iso_date = poll_date.isoformat()
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM answers WHERE chat_id = ? AND poll_date = ? AND user_id = ?",
                (chat_id, iso_date, user_id),
            )
            for option_id in option_ids:
                await db.execute(
                    "INSERT OR IGNORE INTO answers (poll_date, chat_id, user_id, user_name, option_id) VALUES (?, ?, ?, ?, ?)",
                    (iso_date, chat_id, user_id, user_name, option_id),
                )
            await db.commit()

    async def delete_user_answers(self, user_id: int) -> int:
        async with self._connect() as db:
            cursor = await db.execute("DELETE FROM answers WHERE user_id = ?", (user_id,))
            deleted = cursor.rowcount
            cursor = await db.execute(
                "DELETE FROM activity_answers WHERE user_id = ?", (user_id,)
            )
            deleted += cursor.rowcount
            await db.commit()
            return deleted

    async def delete_answers_before(self, cutoff: date) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM answers WHERE poll_date < ?",
                (cutoff.isoformat(),),
            )
            deleted = cursor.rowcount
            await db.execute(
                "DELETE FROM polls WHERE poll_date < ?",
                (cutoff.isoformat(),),
            )
            cursor = await db.execute(
                "DELETE FROM activity_answers WHERE run_date < ?",
                (cutoff.isoformat(),),
            )
            deleted += cursor.rowcount
            await db.execute(
                "DELETE FROM activity_polls WHERE run_date < ?",
                (cutoff.isoformat(),),
            )
            await db.commit()
            return deleted

    async def add_chat(self, chat_id: int, title: str = "") -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO chats (chat_id, title, added_at, active) VALUES (?, ?, ?, 1)
                ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title, active = 1
                """,
                (chat_id, title, date.today().isoformat()),
            )
            await self._ensure_chat_options(db, chat_id)
            await db.commit()

    async def deactivate_chat(self, chat_id: int) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE chats SET active = 0 WHERE chat_id = ? AND active = 1",
                (chat_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def list_chats(self, active_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT chat_id, title, added_at, active FROM chats"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY added_at, chat_id"
        async with self._connect() as db:
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def is_chat_active(self, chat_id: int) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT active FROM chats WHERE chat_id = ?", (chat_id,)
            )
            row = await cursor.fetchone()
        return bool(row and row["active"])

    async def add_activity(
        self,
        chat_id: int,
        title: str,
        description: str,
        days: str,
        run_date: str | None,
        run_time: str,
    ) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                """
                INSERT INTO activities
                    (chat_id, title, description, days, run_date, run_time, active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    chat_id,
                    title,
                    description,
                    days,
                    run_date,
                    run_time,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def list_activities(
        self, chat_id: int, active_only: bool = True
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT id, chat_id, title, description, days, run_date, run_time, "
            "active, created_at FROM activities WHERE chat_id = ?"
        )
        if active_only:
            query += " AND active = 1"
        query += " ORDER BY id"
        async with self._connect() as db:
            cursor = await db.execute(query, (chat_id,))
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def list_active_activities(self) -> list[dict[str, Any]]:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT id, chat_id, title, description, days, run_date, run_time, "
                "active, created_at FROM activities WHERE active = 1 ORDER BY id"
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_activity(self, activity_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT id, chat_id, title, description, days, run_date, run_time, "
                "active, created_at FROM activities WHERE id = ?",
                (activity_id,),
            )
            row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def deactivate_activity(
        self, activity_id: int, chat_id: int | None = None
    ) -> bool:
        query = "UPDATE activities SET active = 0 WHERE id = ? AND active = 1"
        params: list[Any] = [activity_id]
        if chat_id is not None:
            query += " AND chat_id = ?"
            params.append(chat_id)
        async with self._connect() as db:
            cursor = await db.execute(query, params)
            await db.commit()
            return cursor.rowcount > 0

    async def save_activity_poll(
        self, poll_id: str, activity_id: int, chat_id: int, run_date: date
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO activity_polls (poll_id, activity_id, chat_id, run_date)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(poll_id) DO UPDATE SET
                    activity_id = excluded.activity_id,
                    chat_id = excluded.chat_id,
                    run_date = excluded.run_date
                """,
                (poll_id, activity_id, chat_id, run_date.isoformat()),
            )
            await db.commit()

    async def get_activity_poll(self, poll_id: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT poll_id, activity_id, chat_id, run_date "
                "FROM activity_polls WHERE poll_id = ?",
                (poll_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "poll_id": row["poll_id"],
            "activity_id": row["activity_id"],
            "chat_id": row["chat_id"],
            "run_date": date.fromisoformat(row["run_date"]),
        }

    async def set_activity_answer(
        self,
        activity_id: int,
        run_date: date,
        chat_id: int,
        user_id: int,
        user_name: str,
        done: bool,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO activity_answers
                    (activity_id, run_date, chat_id, user_id, user_name, done, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(activity_id, run_date, chat_id, user_id) DO UPDATE SET
                    user_name = excluded.user_name,
                    done = excluded.done,
                    updated_at = excluded.updated_at
                """,
                (
                    activity_id,
                    run_date.isoformat(),
                    chat_id,
                    user_id,
                    user_name,
                    int(done),
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            await db.commit()

    async def delete_activity_answer(
        self, activity_id: int, run_date: date, chat_id: int, user_id: int
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM activity_answers "
                "WHERE activity_id = ? AND run_date = ? AND chat_id = ? AND user_id = ?",
                (activity_id, run_date.isoformat(), chat_id, user_id),
            )
            await db.commit()
