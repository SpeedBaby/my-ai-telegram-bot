"""Тесты слоя AI: сборка контекста, определение лимитов, выбор модели, ретраи."""

import asyncio

import pytest
from google.genai import errors as genai_errors

import ai


def _api_error(status: int):
    """Настоящая ошибка google-genai с заданным HTTP-кодом (создаётся без сети).

    Сигнатура APIError в google-genai 1.x: (code, response_json, response=None).
    Используется реальный класс, а не заглушка, чтобы тесты проверяли именно тот
    путь, по которому ошибка придёт от библиотеки.
    """
    return genai_errors.APIError(
        status,
        {"error": {"code": status, "message": f"HTTP {status}", "status": "ERROR"}},
        None,
    )


def _blob_of(part):
    """Blob с картинкой в разных версиях SDK лежит в inline_data либо в bytes."""
    return getattr(part, "inline_data", None) or getattr(part, "bytes", None)


@pytest.fixture(autouse=True)
def _no_waits(monkeypatch):
    """Ретраи не должны ждать 2/4/6 секунд в тестах."""

    async def instant(*_args, **_kwargs):
        return None

    monkeypatch.setattr(ai.asyncio, "sleep", instant)


# ---------------------------------------------------------------------------
# _build_contents
# ---------------------------------------------------------------------------


def test_build_contents_maps_roles_and_keeps_order():
    contents = ai._build_contents(
        history=[("user", "привет"), ("assistant", "здравствуй")],
        user_message="как дела?",
        image_bytes=None,
        image_mime=None,
    )

    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[-1].parts[0].text == "как дела?"


def test_build_contents_trims_history_to_the_newest_messages(monkeypatch):
    monkeypatch.setattr(ai, "MAX_HISTORY_MESSAGES", 3)
    history = [(("user" if i % 2 == 0 else "assistant"), f"m{i}") for i in range(10)]

    contents = ai._build_contents(history, "вопрос", None, None)

    # 3 последних сообщения истории + текущий вопрос
    assert len(contents) == 4
    assert contents[0].parts[0].text == "m7"


def test_build_contents_puts_image_before_text():
    contents = ai._build_contents([], "что на фото?", b"fake-bytes", "image/png")

    parts = contents[-1].parts
    assert len(parts) == 2
    assert _blob_of(parts[0]).mime_type == "image/png"
    assert parts[1].text == "что на фото?"


def test_build_contents_defaults_image_mime():
    contents = ai._build_contents([], "описание", b"fake-bytes", None)

    assert _blob_of(contents[-1].parts[0]).mime_type == "image/jpeg"


# ---------------------------------------------------------------------------
# _is_rate_limit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    ["Resource has been exhausted (429)", "RESOURCE_EXHAUSTED", "Error 429: quota"],
)
def test_is_rate_limit_detects_text_markers(message):
    assert ai._is_rate_limit(Exception(message)) is True


def test_is_rate_limit_ignores_unrelated_errors():
    assert ai._is_rate_limit(Exception("some bug")) is False


@pytest.mark.parametrize("code", [429, 503])
def test_is_rate_limit_detects_api_error_codes(code):
    assert ai._is_rate_limit(_api_error(code)) is True


def test_is_rate_limit_is_false_for_other_api_codes():
    assert ai._is_rate_limit(_api_error(400)) is False


# ---------------------------------------------------------------------------
# _pick_alternative / validate_models
# ---------------------------------------------------------------------------


def test_pick_alternative_follows_preferred_order():
    available = ["gemini-2.5-flash", "gemini-3.5-flash", "my-custom-model"]

    assert ai._pick_alternative(available, exclude="gemini-3.8-flash") == "gemini-3.5-flash"


def test_pick_alternative_falls_back_to_any_flash_model():
    available = ["some-pro-model", "brand-new-flash-2030"]

    assert ai._pick_alternative(available, exclude="") == "brand-new-flash-2030"


def test_pick_alternative_returns_none_when_only_the_excluded_model_exists():
    assert ai._pick_alternative(["gemini-3.8-flash"], exclude="gemini-3.8-flash") is None


def test_validate_models_switches_primary_when_name_is_retired(monkeypatch):
    monkeypatch.setattr(ai, "PRIMARY_MODEL", "gemini-3.8-flash")
    monkeypatch.setattr(ai, "FALLBACK_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setattr(ai, "list_available_models", lambda: ["gemini-2.5-flash"])

    ai.validate_models()

    assert ai.PRIMARY_MODEL == "gemini-2.5-flash"
    assert ai.FALLBACK_MODEL != ai.PRIMARY_MODEL


def test_validate_models_drops_fallback_that_equals_primary(monkeypatch):
    monkeypatch.setattr(ai, "PRIMARY_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(ai, "FALLBACK_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(
        ai, "list_available_models", lambda: ["gemini-2.5-flash", "gemini-3.5-flash"]
    )

    ai.validate_models()

    assert ai.FALLBACK_MODEL == ""


def test_validate_models_keeps_names_when_api_unreachable(monkeypatch):
    monkeypatch.setattr(ai, "PRIMARY_MODEL", "configured-primary")
    monkeypatch.setattr(ai, "FALLBACK_MODEL", "configured-fallback")
    monkeypatch.setattr(ai, "list_available_models", lambda: [])

    ai.validate_models()

    assert ai.PRIMARY_MODEL == "configured-primary"
    assert ai.FALLBACK_MODEL == "configured-fallback"


# ---------------------------------------------------------------------------
# _generate_sync
# ---------------------------------------------------------------------------


def test_generate_sync_returns_model_text(fake_gemini):
    fake = fake_gemini({"m": "  готовый ответ  "})

    assert ai._generate_sync("m", []) == "готовый ответ"
    assert fake.calls[0]["model"] == "m"


def test_generate_sync_sends_system_prompt_and_history(fake_gemini):
    fake = fake_gemini({"m": "ответ"})
    contents = ai._build_contents([("user", "история")], "вопрос", None, None)

    ai._generate_sync("m", contents)

    assert fake.calls[0]["config"].system_instruction == ai.SYSTEM_PROMPT
    assert fake.calls[0]["contents"] == contents


def test_generate_sync_raises_ai_error_when_model_returns_nothing(fake_gemini):
    fake_gemini({"m": "   "})

    with pytest.raises(ai.AIError):
        ai._generate_sync("m", [])


# ---------------------------------------------------------------------------
# ask_ai
# ---------------------------------------------------------------------------


def test_ask_ai_returns_answer_and_passes_history(fake_gemini):
    fake = fake_gemini({ai.PRIMARY_MODEL: "ответ"})

    result = asyncio.run(ai.ask_ai("вопрос", [("user", "прошлое")]))

    assert result == "ответ"
    assert len(fake.calls) == 1
    assert len(fake.calls[0]["contents"]) == 2  # история + текущий вопрос


def test_ask_ai_falls_back_to_second_model(fake_gemini, one_retry):
    fake_gemini({
        ai.PRIMARY_MODEL: RuntimeError("сервер недоступен"),
        ai.FALLBACK_MODEL: "ответ от запасной модели",
    })

    result = asyncio.run(ai.ask_ai("привет", []))

    assert result == "ответ от запасной модели"


def test_ask_ai_does_not_try_fallback_when_it_equals_primary(
    fake_gemini, one_retry, monkeypatch
):
    monkeypatch.setattr(ai, "FALLBACK_MODEL", ai.PRIMARY_MODEL)
    fake = fake_gemini({ai.PRIMARY_MODEL: "ответ"})

    assert asyncio.run(ai.ask_ai("привет", [])) == "ответ"
    assert {call["model"] for call in fake.calls} == {ai.PRIMARY_MODEL}


def test_ask_ai_reports_exhausted_quota(fake_gemini, one_retry):
    fake_gemini({
        ai.PRIMARY_MODEL: Exception("429 RESOURCE_EXHAUSTED"),
        ai.FALLBACK_MODEL: Exception("429 RESOURCE_EXHAUSTED"),
    })

    with pytest.raises(ai.AIError) as excinfo:
        asyncio.run(ai.ask_ai("привет", []))

    assert "лимит" in str(excinfo.value).lower()


def test_ask_ai_reports_bad_api_key(fake_gemini, one_retry):
    fake_gemini({ai.PRIMARY_MODEL: _api_error(401), ai.FALLBACK_MODEL: _api_error(401)})

    with pytest.raises(ai.AIError) as excinfo:
        asyncio.run(ai.ask_ai("привет", []))

    assert "GEMINI_API_KEY" in str(excinfo.value)


def test_ask_ai_reports_generic_failure(fake_gemini, one_retry):
    fake_gemini({ai.PRIMARY_MODEL: RuntimeError("?", ), ai.FALLBACK_MODEL: RuntimeError("?")})

    with pytest.raises(ai.AIError):
        asyncio.run(ai.ask_ai("привет", []))


def test_ask_ai_retries_rate_limited_request_before_failing(fake_gemini, monkeypatch):
    monkeypatch.setattr(ai, "MAX_RETRIES", 2)
    fake = fake_gemini({ai.PRIMARY_MODEL: [Exception("429"), "со второй попытки"]})

    result = asyncio.run(ai.ask_ai("привет", []))

    assert result == "со второй попытки"
    assert len(fake.calls) == 2
