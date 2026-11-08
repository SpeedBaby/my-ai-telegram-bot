"""
Генерация изображений и видео через Gemini API (Imagen и Veo).

Использует тот же GEMINI_API_KEY, что и чат (ai.py). Работает на Render (США);
из некоторых регионов Google может блокировать генерацию — тогда бот вернёт
понятное сообщение об ошибке.

Названия моделей можно переопределить через переменные окружения:
    IMAGE_MODEL   (например imagen-4.0-generate-001)
    VIDEO_MODEL   (например veo-3.1-generate-001)
Если не заданы — модуль сам подбирает доступную модель из списка моделей ключа.
"""

import asyncio
import logging
import os

import httpx
from google.genai import types

import ai

log = logging.getLogger("media")

IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "").strip()
VIDEO_MODEL = os.environ.get("VIDEO_MODEL", "").strip()

# Порядок предпочтения, если переменная окружения не задана. Google переименовывает
# модели, поэтому на первом месте актуальные, дальше — запасные.
_IMAGE_PREFERRED = (
    "imagen-4.0-generate-001",
    "imagen-4.0-generate-002",
    "imagen-3.0-generate-002",
)
_VIDEO_PREFERRED = (
    "veo-3.1-generate-001",
    "veo-3.0-generate-001",
    "veo-2.0-generate-001",
)

# Telegram не принимает видео тяжелее 50 МБ.
MAX_VIDEO_BYTES = 50 * 1024 * 1024


def _pick_model(available: list[str], keyword: str, env_override: str,
                preferred: tuple[str, ...]) -> str:
    """Подобрать модель: переменная окружения -> известные хорошие -> по ключевому слову."""
    if env_override:
        return env_override
    for name in preferred:
        if name in available:
            return name
    for name in available:
        if keyword in name.lower():
            return name
    # Не нашли в списке — пробуем дефолт; при 404 отдадим понятную ошибку.
    return preferred[0]


def _image_model(api_client) -> str:
    return _pick_model(
        ai.list_available_models(api_client), "imagen", IMAGE_MODEL, _IMAGE_PREFERRED
    )


def _video_model(api_client) -> str:
    return _pick_model(
        ai.list_available_models(api_client), "veo", VIDEO_MODEL, _VIDEO_PREFERRED
    )


def _generate_image_sync(api_client, prompt: str) -> tuple[bytes, str]:
    model = _image_model(api_client)
    log.info("Generating image with model %s", model)
    response = api_client.models.generate_images(
        model=model,
        prompt=prompt,
        config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio="1:1"),
    )
    for generated in (getattr(response, "generated_images", None) or []):
        img = getattr(generated, "image", None)
        if img is not None and getattr(img, "image_bytes", None):
            return img.image_bytes, img.mime_type or "image/png"
    raise ai.AIError(
        "Не удалось сгенерировать изображение — возможно, сработал фильтр "
        "безопасности. Попробуй иначе сформулировать запрос."
    )


async def generate_image(prompt: str) -> tuple[bytes, str]:
    """Сгенерировать картинку по текстовому описанию. Возвращает (bytes, mime)."""
    try:
        return await asyncio.to_thread(_generate_image_sync, ai.client, prompt)
    except ai.AIError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("Image generation failed")
        raise ai.AIError(
            "Не удалось сгенерировать изображение. Возможно, модель недоступна "
            "для твоего ключа или исчерпан лимит. Попробуй позже."
        ) from exc


async def _download(uri: str) -> bytes:
    if uri.startswith("gs://"):
        uri = "https://storage.googleapis.com/" + uri[len("gs://"):]
    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        r = await client.get(uri)
        r.raise_for_status()
        return r.content


async def generate_video(prompt: str) -> tuple[bytes, str]:
    """Сгенерировать короткое видео по описанию. Возвращает (bytes, mime).

    Veo генерирует асинхронно: запускаем задачу и опрашиваем статус, пока видео
    не будет готово (обычно 1–3 минуты).
    """
    model = _video_model(ai.client)
    log.info("Generating video with model %s", model)

    operation = await asyncio.to_thread(
        ai.client.models.generate_videos,
        model=model,
        source=types.GenerateVideosSource(prompt=prompt),
        config=types.GenerateVideosConfig(duration_seconds=8, generate_audio=True),
    )

    for _ in range(30):  # до ~5 минут
        if getattr(operation, "done", None):
            break
        await asyncio.sleep(10)
        operation = await asyncio.to_thread(ai.client.operations.get, operation)

    if not getattr(operation, "done", None):
        raise ai.AIError("Генерация видео заняла слишком много времени. Попробуй ещё раз.")

    if getattr(operation, "error", None):
        raise ai.AIError("Не удалось сгенерировать видео. Попробуй иначе сформулировать.")

    result = getattr(operation, "result", None) or getattr(operation, "response", None)
    videos = getattr(result, "generated_videos", None) or []
    if not videos:
        raise ai.AIError(
            "Видео не сгенерировалось (возможно, сработал фильтр). Попробуй иначе."
        )

    video = getattr(videos[0], "video", None)
    if video is None:
        raise ai.AIError("Видео не сгенерировалось. Попробуй ещё раз.")

    mime = getattr(video, "mime_type", None) or "video/mp4"
    if getattr(video, "video_bytes", None):
        return video.video_bytes, mime

    uri = getattr(video, "uri", None)
    if not uri:
        raise ai.AIError("Видео не содержит данных. Попробуй ещё раз.")

    data = await _download(uri)
    if len(data) > MAX_VIDEO_BYTES:
        raise ai.AIError(
            "Видео получилось слишком большим для Telegram (больше 50 МБ). "
            "Попробуй более короткое описание."
        )
    return data, mime
