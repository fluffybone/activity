# Бот ежедневной активности

Telegram-бот присылает в подключённые группы ежедневный опрос в 20:00,
запоминает ответы каждого пользователя и присылает график в виде HTML-файла:
строки — пользователи, столбцы — даты. Клетка дня делится на цветные
прямоугольники — по одному на каждый выбранный вариант.

## Возможности

- Ежедневный нативный опрос Telegram (неанонимный, с несколькими ответами):
  чекбоксы, смена голоса и видно, кто что выбрал, в реальном времени.
- Варианты настраиваются командами `/addoption`, `/deloption`, `/options`
  (до 12 активных вариантов — ограничение Telegram).
- График за неделю, месяц, год или произвольный период (`/week`, `/month`,
  `/year`, `/chart`).
- Автоматический график за неделю в конце недели (по умолчанию вс 21:00).
- Несколько групп: новая группа подключается автоматически, когда бота туда
  добавляют. У каждой группы свои варианты и свой график — данные разных групп
  не пересекаются.
- Активности с расписанием: бот публикует неанонимный опрос с заданием
  (например, «10 приседаний») в выбранные дни и время. Видно, кто отметил
  «Готово», а повторное нажатие снимает отметку.
- Каждый понедельник утром — короткое мотивационное сообщение о пользе
  движения. 30 текстов сменяются по неделям.
- Данные хранятся в SQLite; при скрытии варианта история сохраняется.
- В БД сохраняется только имя (без фамилии и `@username`); `/delete_me` удаляет
  все ваши отметки, а `RETENTION_DAYS` настраивает автоочистку старых данных.

## Быстрый старт

1. Создайте бота у [@BotFather](https://t.me/BotFather) и получите токен.
2. Отключите privacy mode, иначе бот не увидит команды в группе:
   `/setprivacy` → выберите бота → `Disable`. Либо выдайте боту права
   администратора группы.
3. Добавьте бота в группу — она подключится к рассылке автоматически.
4. Создайте окружение и установите зависимости:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

5. Заполните `.env`:

```dotenv
BOT_TOKEN=123456789:AA...
ADMIN_IDS=123456789
TIMEZONE=Europe/Moscow
```

`GROUP_CHAT_ID` необязателен — его можно указать, если нужно подключить
конкретную группу сразу, не добавляя бота заново.

`ADMIN_IDS` — Telegram ID владельца бота (через запятую). Ему доступны
`/chats` и `/unregister`. Варианты опроса могут менять и администраторы групп.
Узнать свой ID можно у бота [@userinfobot](https://t.me/userinfobot).

6. Запустите:

```bash
python bot.py
```

При первом запуске создаётся `activity.db` и варианты по умолчанию:
Тренировка, Шаги, Танцы, Падел, Отдых, На ленивом. Их можно менять
командами `/addoption` и `/deloption`.

## Команды

Пользователю:

- `/today` — график за сегодня;
- `/week` — график за текущую неделю;
- `/month` — график за текущий месяц;
- `/year` — график за текущий год по месяцам;
- `/chart 2026-10-01 2026-10-31` — график за период;
- `/chart today|week|month|year` — то же, что команды выше;
- `/my` — свой выбор за сегодня (можно `/my 2026-10-19`);
- `/delete_me` — удалить свои отметки из базы (голос в самом опросе Telegram
  при этом остаётся);
- `/options` — варианты опроса текущей группы;
- `/id` — ID чата;
- `/help` — справка.

Администратору группы (или владельцу бота):

- `/addoption Спорт` — добавить вариант (цвет назначится автоматически);
- `/addoption Спорт #3498DB` — добавить с явным цветом;
- `/deloption 3` или `/deloption Спорт` — скрыть вариант;
- `/poll` — отправить опрос сейчас в текущую группу (из лички владельца — во все), не дожидаясь 20:00;
- `/activity add` — создать активность с расписанием;
- `/activity` — список активностей группы;
- `/activity del ID` — удалить активность;
- `/motivation` — показать мотивацию недели;
- `/cancel` — отменить создание активности.

Только владельцу бота (`ADMIN_IDS`):

- `/chats` — список групп в рассылке;
- `/unregister` — убрать текущую группу из рассылки.

Если `ADMIN_IDS` пуст, команды владельца недоступны — укажите там свой
Telegram ID.

## Активности

`/activity add` запускает диалог: название → описание и задание → расписание
(будни, ежедневно, разово или выбранные дни недели) → время. В назначенное
время бот отправляет в группу неанонимный опрос с вариантами «Готово» и
«Не готово».

Отметка «Готово» видна всем участникам, а повторное нажатие снимает голос.
Ответы сохраняются в БД. `/activity` показывает список активностей группы,
`/activity del ID` удаляет активность; уже сохранённые ответы при этом
остаются. Создавать и удалять активности могут администраторы группы или
владелец бота.

## Настройки

Все параметры задаются в `.env`:

| Переменная | По умолчанию | Описание |
|---|---|---|
| `BOT_TOKEN` | — | Токен от BotFather |
| `GROUP_CHAT_ID` | пусто | Необязательный ID группы для подключения сразу |
| `ADMIN_IDS` | пусто | ID владельца бота (через запятую); ему доступны `/chats` и `/unregister` |
| `TIMEZONE` | `Europe/Moscow` | Часовой пояс расписания |
| `POLL_HOUR` / `POLL_MINUTE` | `20` / `0` | Время ежедневного опроса |
| `WEEKLY_REPORT_DAY` | `sun` | День недельного отчёта (`mon`…`sun`) |
| `WEEKLY_REPORT_HOUR` / `WEEKLY_REPORT_MINUTE` | `21` / `0` | Время отчёта |
| `MOTIVATION_DAY` | `mon` | День мотивационного сообщения (`mon`…`sun`) |
| `MOTIVATION_HOUR` / `MOTIVATION_MINUTE` | `9` / `0` | Время мотивационного сообщения |
| `RETENTION_DAYS` | `0` | Хранить ответы N дней (`0` — бессрочно) |
| `DB_PATH` | `activity.db` | Путь к SQLite |

## Развёртывание на VPS

Требования: Linux (Ubuntu/Debian), Python 3.10+, git и systemd. Бот работает
через long polling, поэтому домен, HTTPS и открытые входящие порты не нужны.

### 1. Подготовка сервера

```bash
sudo apt update
sudo apt install -y python3 python3-venv git
sudo adduser --system --group activity
sudo mkdir -p /opt/activity
sudo chown activity:activity /opt/activity
```

### 2. Код и зависимости

```bash
sudo git clone <REPO_URL> /opt/activity
cd /opt/activity
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env
chmod 600 .env
sudo chown -R activity:activity /opt/activity
```

В `.env` укажите `BOT_TOKEN`, `ADMIN_IDS` и при необходимости `TIMEZONE`.
Файл содержит секреты и в git не попадает (см. `.gitignore`).

### 3. systemd

Создайте `/etc/systemd/system/activity-bot.service`:

```ini
[Unit]
Description=Activity Telegram bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/activity
ExecStart=/opt/activity/.venv/bin/python /opt/activity/bot.py
Restart=always
RestartSec=5
User=activity

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now activity-bot
sudo systemctl status activity-bot
sudo journalctl -u activity-bot -f
```

### 4. CI/CD через GitHub Actions

Файл `.github/workflows/deploy.yml` при push в `main` (или ручном запуске)
заходит на сервер по SSH, делает `git pull`, обновляет зависимости и
перезапускает сервис.

Один раз создайте на сервере отдельный ключ для деплоя:

```bash
ssh-keygen -t ed25519 -C github-actions -f ~/.ssh/gh_actions -N ""
cat ~/.ssh/gh_actions.pub >> ~/.ssh/authorized_keys
chmod 700 ~/.ssh
chmod 600 ~/.ssh/authorized_keys
cat ~/.ssh/gh_actions
```

Публичный ключ остаётся на сервере, приватный добавьте в GitHub:
Settings → Secrets and variables → Actions.

| Secret | Значение |
|---|---|
| `SSH_HOST` | IP или домен сервера |
| `SSH_USER` | пользователь для деплоя (например `root`) |
| `SSH_PORT` | SSH-порт (обычно `22`) |
| `SSH_KEY` | приватный ключ `gh_actions` целиком |

После этого запустите workflow вручную (Actions → Deploy → Run workflow)
или сделайте `git push` в `main`. Сервис работает под пользователем
`activity`, поэтому каталог `/opt/activity` и `.env` должны быть ему доступны.

### Обновление и диагностика

Обычное обновление — push в `main` (workflow сделает всё сам). Вручную:

```bash
sudo systemctl restart activity-bot
sudo systemctl status activity-bot
sudo journalctl -u activity-bot -n 50 --no-pager
```

## Приватность

Бот хранит в `activity.db` Telegram ID, имя (без фамилии) и отметки активности.
Данные привязаны к группе и не видны другим группам — в график одной группы
попадают только её участники. Всё лежит на вашем сервере.

Сами опросы неанонимные: участники группы видят, кто и что выбрал, прямо в
опросе Telegram. `/delete_me` убирает отметки из базы (и из будущих графиков),
но уже отданный голос в сообщении опроса остаётся. Администратор может
ограничить срок хранения через `RETENTION_DAYS` (очистка старых записей
запускается ночью). Стоит предупредить участников, какие данные собираются и
что голоса видны группе.

## Как устроено

- `bot.py` — команды, опрос, расписание (APScheduler).
- `db.py` — SQLite: варианты, ответы, подключённые группы.
- `charts.py` — сборка HTML-диаграммы с цветными блоками.
- `motivation.py` — тексты еженедельной мотивации.
- `config.py` — чтение `.env` и валидация настроек.

Схема БД:

- `options(id, chat_id, label, color, position, active)` — варианты группы;
- `answers(poll_date, chat_id, user_id, user_name, option_id)` — отметки;
- `polls(poll_id, chat_id, poll_date, option_ids)` — связь опроса Telegram
  с датой и вариантами;
- `activities(id, chat_id, title, description, days, run_date, run_time, active, created_at)` — активности и их расписание;
- `activity_polls(poll_id, activity_id, chat_id, run_date)` — связь опроса
  активности с запуском;
- `activity_answers(activity_id, run_date, chat_id, user_id, user_name, done, updated_at)` — отметки «Готово»;
- `chats(chat_id, title, added_at, active)` — группы в рассылке.
