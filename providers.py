"""
Резервные AI-провайдеры с OpenAI-совместимым API.

Основной провайдер — Gemini (см. ai.py). Когда он недоступен или исчерпал
бесплатный лимит, бот обращается сюда: к Groq (бесплатный тариф) или к любому
другому сервису с тем же форматом (xAI Grok, OpenRouter, Ollama и т.п.) —
достаточно поменять GROQ_BASE_URL и GROQ_MODEL.

Зависимостей не добавляет: используется уже установленный httpx.
"""

import base64
import logging
import os

import httpx

log = logging.getLogger("providers")

# Ключ Groq: https://console.groq.com/keys (бесплатный тариф, карта не нужна).
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

# Любой OpenAI-совместимый адрес. Для xAI Grok: https://api.x.ai/v1
GROQ_BASE_URL = (
    os.environ.get("GROQ_BASE_URL") or "https://api.groq.com/openai/v1"
).rstrip("/")

# Модель для текста и отдельная для картинок (не все модели видят изображения).
GROQ_MODEL = os.environ.get("GROQ_MODEL") or "llama-3.3-70b-versatile"
GROQ_VISION_MODEL = (
    os.environ.get("GROQ_VISION_MODEL") or "meta-llama/llama-4-scout-17b-16e-instruct"
)

GROQ_TIMEOUT = float(os.environ.get("GROQ_TIMEOUT", "60"))
_MAX_OUTPUT_TOKENS = int(os.environ.get("AI_MAX_OUTPUT_TOKENS", "2048"))


class ProviderError(Exception):
    """Провайдер не смог дать ответ (сеть, лимит, неверный ключ)."""


def is_configured() -> bool:
    """Настроен ли резервный провайдер (задан ли ключ)."""
    return bool(GROQ_API_KEY)


def _build_messages(
    history: list[tuple[str, str]],
    user_message: str,
    system_prompt: str,
    image_bytes: bytes | None = None,
    image_mime: str | None = None,
) -> list[dict]:
    """Собрать messages в формате OpenAI из нашей истории (user/assistant)."""
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    for role, text in history:
        # В базе роли "user"/"assistant" — формат OpenAI их уже понимает.
        messages.append(
            {"role": "user" if role == "user" else "assistant", "content": text}
        )

    if image_bytes:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        mime = image_mime or "image/jpeg"
        content: object = [
            {"type": "text", "text": user_message},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}"},
            },
        ]
    else:
        content = user_message

    messages.append({"role": "user", "content": content})
    return messages


async def ask(
    history: list[tuple[str, str]],
    user_message: str,
    system_prompt: str,
    image_bytes: bytes | None = None,
    image_mime: str | None = None,
) -> str:
    """Спросить резервную модель. Бросает ProviderError при любой неудаче."""
    if not is_configured():
        raise ProviderError("GROQ_API_KEY не задан")

    model = GROQ_VISION_MODEL if image_bytes else GROQ_MODEL
    payload = {
        "model": model,
        "messages": _build_messages(
            history, user_message, system_prompt, image_bytes, image_mime
        ),
        "temperature": 0.7,
        "max_tokens": _MAX_OUTPUT_TOKENS,
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}

    try:
        async with httpx.AsyncClient(timeout=GROQ_TIMEOUT) as client:
            response = await client.post(
                f"{GROQ_BASE_URL}/chat/completions",
                json=payload,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise ProviderError(f"Сеть недоступна: {type(exc).__name__}: {exc}") from exc

    if response.status_code >= 400:
        # Тело ответа содержит причину (лимит, неверная модель, ключ) — логируем,
        # но пользователю отдаём короткое сообщение.
        detail = response.text[:300]
        log.error("Groq вернул %s: %s", response.status_code, detail)
        raise ProviderError(f"Groq ответил кодом {response.status_code}")

    try:
        data = response.json()
        choice = data["choices"][0]
        text = (choice["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ProviderError("Неожиданный формат ответа Groq") from exc

    if not text:
        raise ProviderError("Groq вернул пустой ответ")
    if choice.get("finish_reason") == "length":
        # Ответ упёрся в max_tokens: отправляем пользователю то, что есть,
        # но в логе видно, что генерация не помещается в лимит токенов.
        log.warning(
            "Groq обрезал ответ по лимиту токенов (model=%s, max_tokens=%s)",
            model,
            _MAX_OUTPUT_TOKENS,
        )
    return text
