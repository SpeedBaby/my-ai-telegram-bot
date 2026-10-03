"""
Быстрая самопроверка перед запуском и деплоем.

    python check_setup.py

Проверяет по-настоящему:
  1. BOT_TOKEN — реальный запрос getMe к Telegram.
  2. GEMINI_API_KEY и имя модели — реальный генерационный запрос к Gemini.
  3. Состояние webhook — чтобы понять, в каком режиме работает бот.

Ничего не меняет, можно запускать сколько угодно раз.
Внимание: запрос к Gemini тратит одну строку бесплатного лимита.
"""

import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv


async def check_telegram(token: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = r.json()
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] Telegram недоступен: {type(exc).__name__}: {exc}")
        return False

    if not data.get("ok"):
        print(f"[FAIL] Telegram отверг BOT_TOKEN: {data.get('description')}")
        return False

    me = data["result"]
    print(f"[OK]   Telegram: бот @{me['username']} ({me['first_name']})")

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getWebhookInfo")
            info = r.json().get("result", {})
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Не удалось проверить webhook: {exc}")
        return True

    if info.get("url"):
        print(f"[INFO] Webhook установлен: {info['url']}")
        if info.get("last_error_message"):
            print(f"[WARN] Telegram жалуется: {info['last_error_message']}")
    else:
        print("[INFO] Webhook не установлен — будет работать long polling "
              "(это правильный режим для `python bot.py`).")
    return True


async def check_gemini(key: str) -> bool:
    model = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        print(f"[FAIL] Не установлена библиотека google-genai: {exc}")
        return False

    try:
        client = genai.Client(api_key=key)
        response = await client.aio.models.generate_content(
            model=model,
            contents="Ответь ровно одно слово: ок",
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=10,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        print(f"[FAIL] Gemini, модель {model}: {type(exc).__name__}: {text[:400]}")
        if "404" in text or "not found" in text.lower():
            print("       -> проверь GEMINI_MODEL: https://ai.google.dev/gemini-api/docs/models")
        elif "400" in text or "API key" in text:
            print("       -> проверь GEMINI_API_KEY: https://aistudio.google.com/apikey")
        return False

    answer = (response.text or "").strip()
    if answer:
        print(f"[OK]   Gemini {model}: {answer[:60]!r}")
        return True

    print(f"[WARN] Gemini вернул пустой текст (модель {model} жива, но ответ пустой)")
    return True


async def main() -> int:
    load_dotenv()
    print(f"Python {sys.version.split()[0]}\n")

    token = os.environ.get("BOT_TOKEN", "").strip()
    key = os.environ.get("GEMINI_API_KEY", "").strip()

    ok = True

    if not token:
        print("[FAIL] BOT_TOKEN не задан. Скопируй .env.example в .env и заполни.")
        ok = False
    else:
        ok &= await check_telegram(token)

    if not key:
        print("[FAIL] GEMINI_API_KEY не задан. Ключ: https://aistudio.google.com/apikey")
        ok = False
    else:
        ok &= await check_gemini(key)

    print()
    if ok:
        print("Всё готово. Запуск бота:  python bot.py")
        return 0
    print("Есть проблемы — исправь отмеченное [FAIL] и запусти проверку снова.")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nПрервано.")
