"""
Telegram bot: FastAPI webhook server + message handling.

Run on a server (Render):   uvicorn bot:app --host 0.0.0.0 --port $PORT
Run locally (no webhook):   python bot.py     (needs no PUBLIC_URL — long polling)
"""

import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

# Load .env for local runs. On Render variables come from the dashboard.
load_dotenv()

from ai import AIError, ask_ai  # noqa: E402  (must import after load_dotenv)
from memory import add_message, clear_history, get_history  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("ai-bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-me")
WEBHOOK_PATH = f"/telegram/{WEBHOOK_SECRET}"

# Optional: comma-separated Telegram user IDs. Empty = bot is open to everyone.
ALLOWED_USERS = {
    int(x) for x in os.environ.get("ALLOWED_USERS", "").replace(" ", "").split(",") if x
}

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

# Guard against huge downloads/uploads on a free 512 MB host.
MAX_PHOTO_BYTES = 10 * 1024 * 1024
UPLOAD_TIMEOUT = httpx.Timeout(120.0, connect=15.0)

# Keep references to background tasks so the GC does not cancel them mid-flight.
_background_tasks: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    """Start a background task, keep a reference and log any unhandled error."""

    def _done(task: asyncio.Task):
        _background_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error:
            log.exception("Background task failed", exc_info=error)

    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_done)
    return task


async def tg(method: str, **payload):
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f"{TG_API}/{method}", json=payload)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data}")
        return data["result"]


async def send_message(chat_id: int, text: str, reply_to: int | None = None):
    """Send text, splitting by Telegram's 4096-char limit.

    Tries Markdown first; if Telegram rejects the formatting, resends as plain text.
    """
    chunks = _split_text(text, 4000)
    for i, chunk in enumerate(chunks):
        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"}
        if reply_to and i == 0:
            payload["reply_to_message_id"] = reply_to
        try:
            await tg("sendMessage", **payload)
        except RuntimeError:
            payload.pop("parse_mode", None)
            await tg("sendMessage", **payload)


def _split_text(text: str, limit: int) -> list[str]:
    """Split on paragraph boundaries where possible so code blocks are less likely to break."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = paragraph if not current else current + "\n\n" + paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        # A single paragraph larger than the limit: hard split.
        while len(paragraph) > limit:
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


async def send_typing(chat_id: int):
    try:
        await tg("sendChatAction", chat_id=chat_id, action="typing")
    except Exception:  # noqa: BLE001
        pass


async def keep_typing(chat_id: int, stop: asyncio.Event):
    """Telegram shows 'typing...' for ~5 seconds; refresh while the AI is thinking."""
    while not stop.is_set():
        await send_typing(chat_id)
        try:
            await asyncio.wait_for(stop.wait(), timeout=4)
        except asyncio.TimeoutError:
            pass


async def download_telegram_file(file_id: str) -> tuple[bytes, str]:
    file_info = await tg("getFile", file_id=file_id)
    file_path = file_info["file_path"]

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{TG_FILE}/{file_path}")
        r.raise_for_status()

    mime = "image/jpeg"
    lower = file_path.lower()
    if lower.endswith(".png"):
        mime = "image/png"
    elif lower.endswith(".webp"):
        mime = "image/webp"
    elif lower.endswith(".gif"):
        mime = "image/gif"
    return r.content, mime


async def answer_and_reply(
    chat_id: int,
    user_id: int,
    user_text: str,
    memory_text: str,
    image_bytes: bytes | None = None,
    image_mime: str | None = None,
    reply_to: int | None = None,
):
    """Show 'typing...', call the AI, store the exchange, send the answer."""
    stop = asyncio.Event()
    typing_task = spawn(keep_typing(chat_id, stop))
    try:
        history = get_history(user_id)
        try:
            answer = await ask_ai(
                user_message=user_text,
                history=history,
                image_bytes=image_bytes,
                image_mime=image_mime,
            )
        except AIError as exc:
            await send_message(chat_id, f"⚠️ {exc}", reply_to=reply_to)
            return
        except Exception:  # noqa: BLE001
            log.exception("AI error")
            await send_message(
                chat_id,
                "Произошла ошибка при обращении к AI. Проверь настройки API или попробуй позже.",
                reply_to=reply_to,
            )
            return

        add_message(user_id, "user", memory_text)
        add_message(user_id, "assistant", answer)
        await send_message(chat_id, answer, reply_to=reply_to)
    finally:
        stop.set()
        typing_task.cancel()


async def process_update(update: dict):
    # Support both new messages and edited ones.
    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if not chat_id:
        return

    user = message.get("from", {})
    user_id = int(user.get("id", chat_id))
    text = (message.get("text") or message.get("caption") or "").strip()
    message_id = message.get("message_id")

    # Optional private-bot mode: answer only listed users.
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        log.info("Blocked user %s", user_id)
        await send_message(chat_id, "Этот бот приватный. Обратись к его владельцу.")
        return

    # Commands (strip @BotName suffix like "/start@my_bot")
    command = text.split("@")[0].lower() if text.startswith("/") else ""

    if command == "/start":
        await send_message(
            chat_id,
            "Привет! Я твой AI-бот 🤖\n\n"
            "Я умею:\n"
            "• отвечать на вопросы и вести диалог;\n"
            "• распознавать фотографии (в том числе текст и код на фото);\n"
            "• помогать с Python, JavaScript, C++, HTML/CSS и другими языками;\n"
            "• помнить последние сообщения диалога.\n\n"
            "Просто напиши вопрос или пришли фото.\n\n"
            "Команды:\n"
            "/start — эта справка\n"
            "/reset — очистить память диалога\n"
            "/id — твой идентификатор\n"
            "/about — информация о боте",
            reply_to=message_id,
        )
        return

    if command == "/reset":
        clear_history(user_id)
        await send_message(chat_id, "Память текущего диалога очищена 🧹", reply_to=message_id)
        return

    if command == "/id":
        await send_message(chat_id, f"Твой user_id: `{user_id}`", reply_to=message_id)
        return

    if command == "/about":
        await send_message(
            chat_id,
            "Я работаю как Telegram-интерфейс к мультимодальной AI-модели Gemini. "
            "Модель выполняется в облаке, поэтому твой компьютер может быть выключен — "
            "бот крутится на бесплатном хостинге 24/7.",
            reply_to=message_id,
        )
        return

    if text.startswith("/"):
        await send_message(
            chat_id, "Не знаю такую команду. Напиши /start", reply_to=message_id
        )
        return

    # Photos
    photo = message.get("photo")
    if photo:
        # Telegram sends several sizes; the last is normally the largest.
        chosen = photo[-1]
        if chosen.get("file_size", 0) > MAX_PHOTO_BYTES:
            await send_message(
                chat_id,
                "Фотография слишком большая (лимит 10 МБ). Пришли её в меньшем размере.",
                reply_to=message_id,
            )
            return

        file_id = chosen["file_id"]
        caption = text or "Проанализируй эту фотографию подробно. Опиши, что на ней изображено."

        try:
            image_bytes, mime = await download_telegram_file(file_id)
        except Exception:  # noqa: BLE001
            log.exception("Download failed")
            await send_message(
                chat_id, "Не удалось скачать фотографию. Попробуй ещё раз.", reply_to=message_id
            )
            return

        await answer_and_reply(
            chat_id=chat_id,
            user_id=user_id,
            user_text=caption,
            memory_text="[Фотография] " + caption,
            image_bytes=image_bytes,
            image_mime=mime,
            reply_to=message_id,
        )
        return

    if message.get("document"):
        await send_message(
            chat_id,
            "Я пока понимаю только фотографии и текст. Пришли картинку обычным изображением.",
            reply_to=message_id,
        )
        return

    if not text:
        return

    # Basic anti-spam/input limit.
    if len(text) > 12000:
        await send_message(
            chat_id, "Сообщение слишком большое. Раздели его на несколько частей.",
            reply_to=message_id,
        )
        return

    await answer_and_reply(
        chat_id=chat_id,
        user_id=user_id,
        user_text=text,
        memory_text=text,
        reply_to=message_id,
    )


async def set_webhook():
    if not PUBLIC_URL:
        log.warning("PUBLIC_URL is not set; webhook was not configured.")
        return

    webhook_url = f"{PUBLIC_URL}{WEBHOOK_PATH}"
    if not WEBHOOK_SECRET or WEBHOOK_SECRET == "change-me":
        log.error(
            "WEBHOOK_SECRET not set — webhook mode would be insecure. "
            "Set it in Render or use long polling (no PUBLIC_URL)."
        )
        return

    result = await tg(
        "setWebhook",
        url=webhook_url,
        secret_token=WEBHOOK_SECRET,
        allowed_updates=["message", "edited_message"],
        drop_pending_updates=False,
    )
    log.info("Webhook configured: %s", result)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await set_webhook()
    except Exception:
        log.exception("Could not configure Telegram webhook")
    yield


app = FastAPI(title="My AI Telegram Bot", lifespan=lifespan)


@app.get("/", response_class=PlainTextResponse)
async def health():
    return "AI Telegram bot is running."


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    # Never accept updates when the secret was left at its default value.
    if not WEBHOOK_SECRET or WEBHOOK_SECRET == "change-me":
        return PlainTextResponse("webhook secret is not configured", status_code=503)

    # Telegram sends the secret token in this header.
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if not secrets.compare_digest(secret or "", WEBHOOK_SECRET):
        return PlainTextResponse("forbidden", status_code=403)

    update = await request.json()

    # Return 200 immediately; AI processing continues in the background.
    spawn(process_update(update))
    return PlainTextResponse("ok")


@app.get("/health")
async def health_json():
    return {"status": "ok"}


async def run_polling() -> None:
    """Long polling mode: no PUBLIC_URL and no HTTPS tunnel needed (local runs).

    Telegram only delivers updates through getUpdates while no webhook is set,
    so the webhook is removed on startup.
    """
    async with httpx.AsyncClient() as client:
        await client.post(f"{TG_API}/deleteWebhook", json={"drop_pending_updates": True})
        me = (await client.post(f"{TG_API}/getMe")).json().get("result", {})
        log.info("Polling as @%s — press Ctrl+C to stop", me.get("username", "?"))

        offset: int | None = None
        while True:
            payload: dict = {
                "timeout": 50,
                "allowed_updates": ["message", "edited_message"],
            }
            if offset:
                payload["offset"] = offset

            try:
                r = await client.post(f"{TG_API}/getUpdates", json=payload, timeout=65)
                data = r.json()
                if not data.get("ok"):
                    raise RuntimeError(f"getUpdates failed: {data}")
                updates = data["result"]
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("getUpdates error, retry in 5s")
                await asyncio.sleep(5)
                continue

            for update in updates:
                offset = max(offset or 0, update["update_id"] + 1)
                spawn(process_update(update))


if __name__ == "__main__":
    try:
        asyncio.run(run_polling())
    except KeyboardInterrupt:
        log.info("Stopped by user")
