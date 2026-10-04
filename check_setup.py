"""
Быстрая самопроверка перед запуском и деплоем.
про
    python check_setup.py

Проверяет по-настоящему:
  1. BOT_TOKEN — реальный запрос getMe к Telegram.
  2. GEMINI_API_KEY и имя модели — реальный генерационный запрос к Gemini.
  3. Состояние webhook — чтобы понять, в каком режиме работает бот.

Если Telegram недоступен, скрипт сам определяет, это блокировка провайдером
или пропавший интернет, и подсказывает, что делать.

Ничего не меняет, можно запускать сколько угодно раз.
Внимание: запрос к Gemini тратит одну строку бесплатного лимита.
"""

import asyncio
import os
import socket
import sys

import httpx
from dotenv import load_dotenv


def _force_utf8_console() -> None:
    """Windows-консоль по умолчанию не понимает UTF-8 — включаем его.

    Без этого русский текст превращается в кракозябры.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:  # noqa: BLE001
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


_force_utf8_console()

# Хост-«свидетель»: если Google доступен, а Telegram — нет, значит интернет
# работает, а Telegram блокируется провайдером (частая ситуация в РФ).
CONTROL_HOST = "generativelanguage.googleapis.com"


def _tcp_reachable(host: str, port: int = 443, timeout: float = 6.0) -> bool:
    """Открыт ли TCP-порт. Прямая сокет-проверка, без кешей и прокси."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _explain_telegram_block() -> None:
    """Объясняет, почему нет соединения с Telegram, и что с этим делать."""
    if _tcp_reachable(CONTROL_HOST):
        print("       Интернет есть (Google отвечает), но Telegram заблокирован")
        print("       провайдером или файрволом — это не проблема кода и не токена.")
        print()
        print("       Что делать:")
        print("       • Локальный запуск без VPN не заработает — это ожидаемо.")
        print("       • Разверни бота на Render: оттуда Telegram доступен,")
        print("         и бот будет работать 24/7 (SETUP_RU.md, шаги 8–11).")
        print("       • Либо включи VPN и запусти проверку снова.")
    else:
        print("       Google тоже не отвечает — похоже, пропал интернет целиком.")
        print("       Проверь подключение к сети и попробуй снова.")


async def check_telegram(token: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = r.json()
    except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ReadTimeout) as exc:
        # Соединение вообще не устанавливается — это сеть, а не токен.
        print(f"[FAIL] Нет соединения с Telegram ({type(exc).__name__}).")
        _explain_telegram_block()
        return False
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


async def _try_generate(client, types, model: str) -> tuple[str, str | None]:
    """Одна проверка модели: (текст_ответа_или_пусто, текст_ошибки_или_None)."""
    try:
        response = await client.aio.models.generate_content(
            model=model,
            contents="Ответь ровно одно слово: ок",
            config=types.GenerateContentConfig(
                temperature=0.0,
                # У моделей 2.5 «размышления» тоже тратят этот лимит,
                # поэтому берём с запасом, чтобы не получить пустой ответ.
                max_output_tokens=256,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return "", f"{type(exc).__name__}: {exc}"
    return (response.text or "").strip(), None


# Модели, которые не умеют обычный текстовый ответ (озвучка, картинки и т.п.).
_NON_TEXT_MARKERS = (
    "tts", "audio", "image", "embedding", "embed", "aqa",
    "computer-use", "deep-research", "antigravity", "veo", "imagen",
    "live", "vision", "robotics", "learnlm",
)
_GOOD_MODELS = {
    "gemini-2.0-flash", "gemini-2.5-flash",
    "gemini-2.0-flash-lite", "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
    "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-pro-latest",
}


def _looks_like_text_model(name: str) -> bool:
    lower = name.lower()
    return not any(marker in lower for marker in _NON_TEXT_MARKERS)


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
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] Не удалось создать Gemini-клиент: {exc}")
        return False

    # Какие модели реально доступны этому ключу — это сразу проясняет 404.
    available: list[str] = []
    try:
        for m in client.models.list():
            name = (getattr(m, "name", "") or "").removeprefix("models/")
            if name:
                available.append(name)
        if available:
            text_models = sorted(m for m in available if _looks_like_text_model(m))
            print(f"[INFO] Доступно моделей: {len(available)}, "
                  f"из них текстовых: {len(text_models)}.")
            if text_models:
                print(f"       Текстовые: {', '.join(text_models)}")
            if model not in available:
                print(f"[WARN] Заданная GEMINI_MODEL={model!r} в списке отсутствует.")
        else:
            print("[WARN] Список моделей пуст — ключ может быть без доступа к API.")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Не удалось получить список моделей: {type(exc).__name__}: {exc}")

    answer, error = await _try_generate(client, types, model)
    if answer:
        print(f"[OK]   Gemini {model}: {answer[:60]!r}")
        return True

    print(f"[FAIL] Модель {model} не ответила. Пробую подобрать рабочую...")
    # Перебираем текстовые модели и ищем ту, что реально работает.
    candidates = [m for m in available if _looks_like_text_model(m) and m != model]
    # Сначала известные хорошие, потом остальные.
    candidates.sort(key=lambda n: (n not in _GOOD_MODELS, n))
    last_error = error
    for candidate in candidates[:6]:
        print(f"       ... пробую {candidate}")
        text, err = await _try_generate(client, types, candidate)
        if text:
            print(f"[OK]   Рабочая модель найдена: {candidate}")
            print(f"       -> поставь GEMINI_MODEL={candidate} (и в Render тоже)")
            return True
        last_error = err or last_error

    # Самая частая причина: региональная блокировка Google.
    if last_error and (
        "location is not supported" in last_error.lower()
        or "failed_precondition" in last_error.lower()
    ):
        print("       -> Google Gemini API НЕДОСТУПЕН из твоего региона.")
        print("          Это ограничение Google, а не код/ключ. Список стран: "
              "https://ai.google.dev/gemini-api/docs/available-regions")
        print("          Запусти бота на Render (США) или используй VPN.")
    else:
        print("       -> ни одна текстовая модель не ответила. "
              "Проверь ключ: https://aistudio.google.com/apikey")
    return False


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
