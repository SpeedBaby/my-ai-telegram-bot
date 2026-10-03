# Полная установка с нуля

## 0. Что тебе понадобится

- **[Telegram](https://telegram.org)** — аккаунт.
- **[Python 3.11+](https://python.org)** — для локального запуска и разработки.
- **[Google AI Studio](https://aistudio.google.com/apikey)** — бесплатный API key для модели Gemini (текст + фото).
- **[GitHub](https://github.com)** — для хранения кода.
- **[Render](https://render.com)** — бесплатный хостинг, где бот будет работать 24/7.

## 1. Установи Python

1. Скачай и установи Python с [python.org](https://python.org) (поставь галочку *Add Python to PATH*).
2. Открой терминал и проверь:

```
python --version
```

Должно быть `Python 3.11.x` или новее.

## 2. Создай Telegram-бота

1. Открой Telegram и найди **@BotFather**.
2. Отправь команду `/newbot`.
3. Придумай имя (отображается в чате) и username (заканчивается на `bot`, например `my_ai_2026_bot`).
4. BotFather пришлёт токен вида `123456789:ABCdef...`.
5. Скопируй его — он пригодится в шаге 6.

## 3. Получи Gemini API key

1. Открой [Google AI Studio](https://aistudio.google.com/apikey).
2. Нажми **Create API Key**.
3. Скопируй ключ — он пригодится в шаге 6.
4. У бесплатного тарифа есть лимиты на количество запросов в минуту и в день —
   точные цифры для твоего ключа видны на странице
   [aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit).
   Для личного общения их хватает с запасом.
5. Ключ **никогда не публикуй в GitHub**.

## 4. Скачай проект

Если проект ещё не скачан — клонируй репозиторий или распакуй архив.
Открой терминал в папке проекта:

```
cd my_ai_telegram_bot
```

## 5. Установи зависимости

Windows:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 6. Настрой переменные окружения

1. Скопируй `.env.example` в `.env`:

```
copy .env.example .env
```

2. Открой `.env` в текстовом редакторе и замени значения:

```
BOT_TOKEN=123456789:ABCdef...       ← токен от BotFather
GEMINI_API_KEY=...                  ← ключ из Google AI Studio
PUBLIC_URL=                         ← пока оставь пустым!
WEBHOOK_SECRET=очень_длинный_случайный_секрет
GEMINI_MODEL=gemini-3.8-flash
GEMINI_FALLBACK_MODEL=gemini-3.5-flash-lite
```

## 7. Первый запуск (локально)

Когда `PUBLIC_URL` пуст — бот автоматически переключается в **long polling**:

```
python bot.py
```

Или:

```
python polling.py
```

Бот напишет `Polling as @your_bot_username — press Ctrl+C to stop`.
Пиши ему в Telegram — он будет отвечать. Нажми **Ctrl+C**, чтобы остановить.

> **Это НЕ 24/7.** Бот работает пока открыт терминал. Для круглосуточной работы — шаги 8–11.

## 8. Отправь проект в GitHub

1. Создай новый пустой репозиторий на [github.com/new](https://github.com/new).
2. В терминале:

```
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/ТВОЙ_НИК/ТВОЙ_РЕПО.git
git push -u origin main
```

## 9. Деплой на Render

1. Войди на [render.com](https://render.com) (или зарегистрируйся через GitHub).
2. Нажми **New → Blueprint from GitHub Repo**.
3. Выбери свой репозиторий → **Continue**.
4. Render подхватит `render.yaml` автоматически. Проверь:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn bot:app --host 0.0.0.0 --port $PORT`
   - **Plan**: Free
5. В **Advanced** раскрой **Environment Variables** и добавь:

| Ключ | Значение |
| --- | --- |
| `BOT_TOKEN` | токен от BotFather |
| `GEMINI_API_KEY` | ключ из Google AI Studio |
| `PUBLIC_URL` | пока **оставь пустым** — заполнишь после деплоя |
| `WEBHOOK_SECRET` | длинная случайная строка |
| `GEMINI_MODEL` | `gemini-3.8-flash` |
| `GEMINI_FALLBACK_MODEL` | `gemini-3.5-flash-lite` |

6. Нажми **Apply**. Начнётся деплой (3–5 минут).

## 10. Подключи вебхук

1. Когда Render покажет зелёный индикатор (Service is live), скопируй URL сервиса.
   Он вида: `https://my-ai-telegram-bot-xxxx.onrender.com`
2. В настройках сервиса на Render, в разделе **Environment Variables**, впиши:

```
PUBLIC_URL = https://my-ai-telegram-bot-xxxx.onrender.com
```

3. Нажми **Manual Deploy** (или **Advanced → Manual Deploy**), чтобы перезапустить сервис.
4. При запуске бот сам зарегистрирует Telegram webhook — проверь **Logs** (вкладка Logs в Render): должно быть `Webhook configured`.

> **Не добавляй** `/telegram/...` вручную — приложение делает это само.

## 11. Держи бота онлайн 24/7

Render Free **засыпает** после ~15 минут без входящих запросов. Первый ответ после сна будет с задержкой 30–60 секунд. Чтобы бот «просыпался» автоматически:

### Способ: cron-job.org (полностью бесплатно)

1. Зайди на [cron-job.org](https://cron-job.org) → **Register**.
2. Нажми **Add cronjob**.
3. Настройки:
   - **Active**: ✅
   - **URL**: `https://твой-сервис.onrender.com/health`
   - **Interval**: каждые 5 минут
   - **Method**: GET
   - **User-Agent**: `Mozilla/5.0`
4. Нажми **Add cronjob**.

Готово! Каждые 5 минут сервис будет получать лёгкий запрос и «просыпаться».

## 12. Проверка

1. Открой бота в Telegram → `/start`.
2. Напиши: `Напиши программу на Python, которая проверяет простое число.`
3. Отправь фотографию с подписью: `Что на этой фотографии?`
4. Отправь `/id` — бот покажет твой Telegram ID (полезно для приватного режима).

## 13. Команды бота

| Команда | Описание |
| --- | --- |
| `/start` | Справка |
| `/reset` | Очистить историю диалога |
| `/id` | Показать твой user_id |
| `/about` | Информация о боте |

## 14. Приватный режим (по желанию)

Чтобы бот отвечал только тебе:

1. Отправь себе `/id` в боте — получишь свой Telegram ID (число).
2. В Render → Environment Variables → добавь:

```
ALLOWED_USERS = 123456789
```

(через запятую, если несколько пользователей).

## 15. Частые ошибки

| Ошибка | Решение |
| --- | --- |
| `KeyError: 'BOT_TOKEN'` | Проверь, что BOT_TOKEN и GEMINI_API_KEY заданы в Render |
| `404 NOT_FOUND` от Gemini | Проверь название модели — см. [ai.google.dev/gemini-api/docs/models](https://ai.google.dev/gemini-api/docs/models) |
| `429 RESOURCE_EXHAUSTED` | Исчерпан бесплатный лимит. Подожди минуту — бот сам попробует резервную модель |
| `getMe: Unauthorized` | Неверный BOT_TOKEN |
| `blocked` от Telegram | Перезапусти сервис на Render |
| Бот молчит на Render | Открой `https://твой-url/health` в браузере, затем проверь **Logs** в Render |
| Ошибка `Invalid markdown` | Бот автоматически переведёт ответ в plain text |

## 15. Проверка себя тестами (необязательно)

В проекте есть офлайн-тесты — они проверяют команды, память, разбор фото и
поведение при лимитах, не обращаясь к Telegram и Gemini:

```
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

Все тесты должны быть зелёными. Если какой-то упал — посмотри его название,
оно описывает сломанное поведение по-русски.

## 16. Куда девается память диалога

История хранится в файле `memory.db` на бесплатном Render. Когда Render
пересоздаёт экземпляр сервиса (например, после редиплоя), история обнуляется.
Это нормально: бот продолжает работать, просто перестаёт помнить прошлые реплики.
Если нужна постоянная память — позже подключают бесплатную PostgreSQL (Neon/Supabase).

## 17. Что можно добавить дальше

- **RAG** — загрузи свои PDF/статьи, бот будет отвечать по ним;
- **Google Search** — бот будет искать актуальную информацию;
- **Голосовые сообщения** — распознавание и синтез речи;
- **Inline-кнопки** — удобные быстрые команды;
- **Статистика** — сколько сообщений отправлено, за сколько секунд ответ;
- **Обучение на примерах** — few-shot prompt через систему инструкций.
