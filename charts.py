from __future__ import annotations

import html
import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

MONTHS_GENITIVE = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]

MONTHS_NOMINATIVE = [
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
]

MONTHS_SHORT = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]

WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

FALLBACK_COLOR = "#9AA4B2"
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")

CSS = """
:root {
    color-scheme: light;
}
* {
    box-sizing: border-box;
}
body {
    margin: 0;
    padding: 24px;
    background: #eef1f5;
    color: #1b2430;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
}
.wrap {
    max-width: 1500px;
    margin: 0 auto;
    padding: 24px;
    background: #ffffff;
    border-radius: 16px;
    box-shadow: 0 10px 30px rgba(20, 30, 50, 0.08);
}
h1 {
    margin: 0 0 4px;
    font-size: 22px;
    font-weight: 700;
}
.subtitle {
    margin: 0 0 18px;
    color: #6b7785;
    font-size: 13px;
}
.scroll {
    overflow-x: auto;
}
table {
    border-collapse: separate;
    border-spacing: 0;
    --cw: 48px;
}
table.fit {
    width: 100%;
    table-layout: fixed;
}
table.fit .corner,
table.fit .user {
    width: 180px;
}
table.fit .day,
table.fit .cell {
    min-width: 0;
}
th,
td {
    padding: 0;
}
.corner {
    padding: 0 14px 8px 4px;
    color: #6b7785;
    font-size: 11px;
    font-weight: 600;
    text-align: left;
}
.day {
    min-width: var(--cw);
    padding: 0 2px 8px;
    color: #6b7785;
    font-size: 11px;
    font-weight: 600;
    text-align: center;
}
.day .dnum {
    display: block;
    color: #1b2430;
    font-size: 12px;
}
.day .dwd {
    display: block;
    color: #95a1ad;
    font-size: 10px;
    font-weight: 500;
}
.user {
    height: 34px;
    padding: 0 14px 0 4px;
    font-size: 13px;
    font-weight: 500;
    text-align: left;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.cell {
    min-width: var(--cw);
    height: 34px;
    padding: 2px;
    border-top: 1px solid #eef1f5;
    border-left: 1px solid #f4f6f9;
}
.cell.weekend {
    background: #fafbfc;
}
.cell-inner {
    display: flex;
    width: 100%;
    height: 100%;
    overflow: hidden;
    border-radius: 5px;
}
.cell.empty .cell-inner {
    background: #f4f6f9;
}
.seg {
    flex: 1 1 0;
}
.seg + .seg {
    box-shadow: -1px 0 0 rgba(255, 255, 255, 0.55);
}
.legend {
    display: flex;
    flex-wrap: wrap;
    gap: 10px 20px;
    margin-top: 20px;
}
.legend-item {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
}
.swatch {
    width: 14px;
    height: 14px;
    border-radius: 4px;
}
.empty-note {
    padding: 32px 0;
    color: #6b7785;
    font-size: 15px;
    text-align: center;
}
"""


def period_label(start: date, end: date) -> str:
    if start == end:
        return f"{start.day} {MONTHS_GENITIVE[start.month - 1]} {start.year}"
    if start.year == end.year and start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS_GENITIVE[start.month - 1]} {start.year}"
    if start.year == end.year:
        return (
            f"{start.day} {MONTHS_GENITIVE[start.month - 1]} – "
            f"{end.day} {MONTHS_GENITIVE[end.month - 1]} {start.year}"
        )
    return (
        f"{start.day}.{start.month:02d}.{start.year} – "
        f"{end.day}.{end.month:02d}.{end.year}"
    )


def _month_columns(start: date, end: date) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        result.append((year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return result


def _option_sort_key(option: dict[str, Any]) -> tuple[int, int]:
    return int(option.get("position") or 0), int(option.get("id") or 0)


def _safe_color(value: Any) -> str:
    color = str(value or "")
    return color.upper() if _HEX_COLOR.match(color) else FALLBACK_COLOR


def _safe_text(value: Any) -> str:
    return html.escape(str(value or ""))


def _safe_attr(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _column_headers(
    start: date, end: date, mode: str
) -> tuple[list[Any], list[str], list[str], list[list[str]], list[bool]]:
    if mode == "monthly":
        columns: list[Any] = _month_columns(start, end)
        main = [MONTHS_SHORT[month - 1] for _, month in columns]
        sub = [f"{year % 100:02d}" for year, _ in columns]
        titles = [f"{MONTHS_NOMINATIVE[month - 1]} {year}" for year, month in columns]
        weekends = [False] * len(columns)
        return columns, main, sub, titles, weekends

    day_count = (end - start).days + 1
    day_columns = [start + timedelta(days=offset) for offset in range(day_count)]
    main = [str(day.day) for day in day_columns]
    sub = [WEEKDAYS_SHORT[day.weekday()] for day in day_columns]
    titles = [
        f"{day.day} {MONTHS_GENITIVE[day.month - 1]} {day.year} ({WEEKDAYS_SHORT[day.weekday()]})"
        for day in day_columns
    ]
    weekends = [day.weekday() >= 5 for day in day_columns]
    return day_columns, main, sub, titles, weekends


def build_chart_html(
    *,
    answers: list[dict[str, Any]],
    options: list[dict[str, Any]],
    start: date,
    end: date,
    mode: str = "daily",
) -> str:
    option_by_id = {int(option["id"]): option for option in options}
    users: dict[int, str] = {}
    by_user: dict[int, dict[date, set[int]]] = defaultdict(lambda: defaultdict(set))

    for row in answers:
        raw_date = row["poll_date"]
        try:
            answer_date = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date))
        except ValueError:
            continue
        if not start <= answer_date <= end:
            continue
        user_id = int(row["user_id"])
        users[user_id] = str(row["user_name"]) or f"ID {user_id}"
        by_user[user_id][answer_date].add(int(row["option_id"]))

    columns, main_labels, sub_labels, column_titles, weekend_flags = _column_headers(
        start, end, mode
    )

    if mode == "monthly":
        cells: dict[int, dict[Any, set[int]]] = {}
        for user_id, dates in by_user.items():
            aggregated: dict[Any, set[int]] = defaultdict(set)
            for answer_date, ids in dates.items():
                aggregated[(answer_date.year, answer_date.month)] |= ids
            cells[user_id] = aggregated
        index_of = {key: index for index, key in enumerate(columns)}
    else:
        cells = {user_id: dict(dates) for user_id, dates in by_user.items()}
        index_of = {day: index for index, day in enumerate(columns)}

    user_rows = sorted(users.items(), key=lambda item: (item[1].casefold(), item[0]))

    selected_ids: set[int] = set()
    for dates in by_user.values():
        for ids in dates.values():
            selected_ids |= ids

    legend_options = [
        option for option in options if option.get("active") or int(option["id"]) in selected_ids
    ]
    legend_options.sort(key=_option_sort_key)

    if columns:
        if mode == "monthly":
            cell_width = 60
        elif len(columns) <= 7:
            cell_width = 56
        elif len(columns) <= 16:
            cell_width = 44
        else:
            cell_width = 32
    else:
        cell_width = 48

    header_cells = [
        f'<th class="day" scope="col"><span class="dnum">{_safe_text(main)}</span>'
        f'<span class="dwd">{_safe_text(sub)}</span></th>'
        for main, sub in zip(main_labels, sub_labels)
    ]
    header_row = (
        '<tr><th class="corner" scope="col">Пользователь</th>'
        + "".join(header_cells)
        + "</tr>"
    )

    body_rows: list[str] = []
    for user_id, user_name in user_rows:
        user_cells = cells.get(user_id, {})
        row_cells: list[str] = []
        for column_index, column_key in enumerate(columns):
            option_ids = user_cells.get(column_key)
            weekend_class = " weekend" if weekend_flags[column_index] else ""
            if not option_ids:
                row_cells.append(
                    f'<td class="cell empty{weekend_class}" title="{_safe_attr(column_titles[column_index])}">'
                    '<div class="cell-inner"></div></td>'
                )
                continue

            ordered = sorted(
                option_ids, key=lambda oid: _option_sort_key(option_by_id.get(oid, {}))
            )
            segments: list[str] = []
            labels: list[str] = []
            for option_id in ordered:
                option = option_by_id.get(option_id, {})
                label = str(option.get("label") or f"#{option_id}")
                labels.append(label)
                segments.append(
                    f'<span class="seg" style="background:{_safe_color(option.get("color"))}"></span>'
                )
            tooltip = f"{column_titles[column_index]}: " + ", ".join(labels)
            row_cells.append(
                f'<td class="cell{weekend_class}" title="{_safe_attr(tooltip)}">'
                f'<div class="cell-inner">{"".join(segments)}</div></td>'
            )
        body_rows.append(
            f'<tr><th class="user" scope="row">{_safe_text(user_name)}</th>'
            + "".join(row_cells)
            + "</tr>"
        )

    if user_rows:
        if mode == "daily" and len(columns) > 7:
            table_attrs = ' class="fit"'
        else:
            table_attrs = f' class="chart" style="--cw:{cell_width}px"'
        table_html = (
            f'<div class="scroll"><table{table_attrs}>'
            f"<thead>{header_row}</thead>"
            f'<tbody>{"".join(body_rows)}</tbody>'
            "</table></div>"
        )
    else:
        table_html = '<p class="empty-note">За выбранный период ответов нет</p>'

    legend_html = ""
    if legend_options:
        items = "".join(
            f'<div class="legend-item">'
            f'<span class="swatch" style="background:{_safe_color(option.get("color"))}"></span>'
            f'<span>{_safe_text(option.get("label"))}</span>'
            f"</div>"
            for option in legend_options
        )
        legend_html = f'<div class="legend">{items}</div>'

    subtitle = f"{period_label(start, end)} · пользователи × даты"
    if mode == "monthly":
        subtitle += " · по месяцам"

    return (
        "<!doctype html>\n"
        '<html lang="ru">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>График активности — {_safe_text(period_label(start, end))}</title>\n"
        f"<style>{CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        '<div class="wrap">\n'
        "<h1>График активности</h1>\n"
        f'<p class="subtitle">{_safe_text(subtitle)}</p>\n'
        f"{table_html}\n"
        f"{legend_html}\n"
        "</div>\n"
        "</body>\n"
        "</html>\n"
    )
