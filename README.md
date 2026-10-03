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

Загрузи проект в GitHub и создай на Render новый Web Service из репозитория.

Build:
pip install -r requirements.txt

Start:
uvicorn bot:app --host 0.0.0.0 --port $PORT

После создания сервиса Render выдаст URL вида:
https://my-ai-telegram-bot.onrender.com

Поставь его в переменную PUBLIC_URL — и бот сам зарегистрирует Telegram webhook.

Быстрый вариант: на Render нажми **New → Blueprint** и укажи репозиторий — он подхватит `render.yaml`.

## Переменные Render

BOT_TOKEN = токен от BotFather
GEMINI_API_KEY = ключ Gemini API
PUBLIC_URL = URL Render без завершающего /
WEBHOOK_SECRET = длинная случайная строка (без неё webhook не включается)
GEMINI_MODEL = gemini-2.5-flash
GEMINI_FALLBACK_MODEL = gemini-2.5-flash-lite
ALLOWED_USERS = пусто (всем) или ID через запятую
MAX_HISTORY_MESSAGES = 20
AI_MAX_RETRIES = 3

## Что делать при ошибках

- `404 NOT_FOUND` от Gemini — проверь точное название модели на странице https://ai.google.dev/gemini-api/docs/models
- `429 RESOURCE_EXHAUSTED` — исчерпан бесплатный лимит запросов, подожди или уменьши `AI_MAX_RETRIES`. Бот сам попробует запасную модель.
- `418` или `blocked` — сработала фильтрация контента Gemini, ответ не отправлен.
- `getMe: Unauthorized` — неверный BOT_TOKEN.
- Бот молчит на Render — открой `https://твой-url/health`, затем Logs в Render; проверь, что `PUBLIC_URL` начинается с `https://` и без `/` в конце.

## Тесты

В проекте есть 77 офлайн-тестов (без реальных запросов к Telegram и Gemini):

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
