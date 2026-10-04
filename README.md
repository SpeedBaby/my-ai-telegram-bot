# My AI Telegram Bot

Telegram-бот на Python + FastAPI + Gemini API.

## Возможности

- ответы на текстовые вопросы;
- анализ фотографий;
- помощь с программированием;
- краткая память диалога;
- Telegram webhook + фильтр секретного заголовка;
- локальный запуск без публичного адреса (long polling);
- бесплатный деплой на Render Free.

## Важное ограничение бесплатного режима

Render Free не является гарантированным production-хостингом. Сервис засыпает примерно через 15 минут без входящих запросов и имеет месячный лимит бесплатных часов. При первом сообщении после сна возможна задержка 30–60 секунд.

Как держать бот в тонусе: GET-запрос на `https://твой-url/health` каждые 5–10 минут. Бесплатные сервисы для этого — cron-job.org или UptimeRobot.

Также бесплатный Gemini API имеет лимиты. Бесплатность и лимиты могут изменяться самим провайдером.

## Локальный запуск

1. Установи Python 3.11+.
2. Создай виртуальное окружение:

Windows:
python -m venv .venv
.venv\Scripts\activate

3. Установи зависимости:

pip install -r requirements.txt

4. Создай `.env` на основе `.env.example` и заполни BOT_TOKEN и GEMINI_API_KEY.
5. Проверь настройки (реально обращается к Telegram и Gemini):

python check_setup.py

6. Если `PUBLIC_URL` не задан, бот сам возьмёт **long polling** — HTTPS-туннель не нужен. Запусти:

python bot.py

Остановка — Ctrl+C.

## Деплой

Загрузи проект в GitHub, затем на Render нажми **New → Blueprint** и укажи
репозиторий — он подхватит `render.yaml`.

Render попросит только `BOT_TOKEN` и `GEMINI_API_KEY`. Остальное настроится само:
`WEBHOOK_SECRET` Render сгенерирует, а адрес сервиса для webhook возьмётся из
автоматической переменной `RENDER_EXTERNAL_URL` — вручную `PUBLIC_URL` задавать
не нужно.

Стандартный ручной вариант (без Blueprint):

Build:
pip install -r requirements.txt

Start:
uvicorn bot:app --host 0.0.0.0 --port $PORT

## Переменные Render

BOT_TOKEN = токен от BotFather (обязательно)
GEMINI_API_KEY = ключ Gemini API (обязательно)
PUBLIC_URL = необязательно; нужен только для своего домена, на Render подставляется сам
WEBHOOK_SECRET = необязательно; Render генерирует случайный
GEMINI_MODEL = gemini-3.8-flash
GEMINI_FALLBACK_MODEL = gemini-3.5-flash-lite
ALLOWED_USERS = пусто (всем) или ID через запятую
MAX_HISTORY_MESSAGES = 20
AI_MAX_RETRIES = 3

## Что делать при ошибках

- `ConnectTimeout` при `python bot.py` — провайдер блокирует Telegram (частая ситуация в РФ). Код и токен ни при чём: разворачивай на Render (оттуда Telegram доступен) или включи VPN. Проверить причину: `python check_setup.py`.
- `404 NOT_FOUND` от Gemini — Google закрыл модель для новых ключей (в тексте ошибки есть подсказка, например `use models/gemini-3.8-flash`). Бот сам перейдёт на рабочую и запомнит её; если нет — поставь `GEMINI_MODEL=gemini-3.8-flash`.
- `429 RESOURCE_EXHAUSTED` — исчерпан бесплатный лимит запросов, подожди или уменьши `AI_MAX_RETRIES`. Бот сам попробует запасную модель.
- `418` или `blocked` — сработала фильтрация контента Gemini, ответ не отправлен.
- Бот отвечает несколько раз на одно сообщение — Telegram повторил доставку, пока Render «просыпался». Бот теперь пропускает повторные `update_id`.
- `getMe: Unauthorized` — неверный BOT_TOKEN.
- Бот молчит на Render — открой `https://твой-url/health`, затем Logs в Render; проверь, что `PUBLIC_URL` начинается с `https://` и без `/` в конце.

## Тесты

В проекте есть 113 офлайн-тестов (без реальных запросов к Telegram и Gemini):

```
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

Тесты покрывают: команды бота, whitelist, разбор фото и лимит размера,
ошибки AI, память диалога, поведение при 400/401/403/404/429 и подбор доступной модели.

## Безопасность

Никогда не публикуй:
- BOT_TOKEN
- GEMINI_API_KEY
- WEBHOOK_SECRET

`.env` уже добавлен в `.gitignore` — в git он не попадёт.
Если токен Telegram утёк (переслал в чат, попал на скриншот, был в файле) —
отправь BotFather `/revoke` и получи новый.
