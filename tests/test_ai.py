"""Тесты слоя AI: сборка контекста, определение лимитов, выбор модели, ретраи."""

import asyncio
from types import SimpleNamespace

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


@pytest.fixture(autouse=True)
def _reset_model_state():
    """_remember_working_model меняет PRIMARY/FALLBACK напрямую — откатываем."""
    original_primary, original_fallback = ai.PRIMARY_MODEL, ai.FALLBACK_MODEL
    yield
    ai.PRIMARY_MODEL, ai.FALLBACK_MODEL = original_primary, original_fallback


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
# _is_region_blocked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "400 FAILED_PRECONDITION. User location is not supported for the API use.",
        "FAILED_PRECONDITION",
    ],
)
def test_is_region_blocked_detects_markers(message):
    assert ai._is_region_blocked(Exception(message)) is True


def test_is_region_blocked_false_for_other_errors():
    assert ai._is_region_blocked(Exception("some random error")) is False


def test_ask_ai_reports_region_block(fake_gemini, one_retry):
    region = Exception("400 FAILED_PRECONDITION. User location is not supported for the API use.")
    fake_gemini({ai.PRIMARY_MODEL: region, ai.FALLBACK_MODEL: region})

    with pytest.raises(ai.AIError) as excinfo:
        asyncio.run(ai.ask_ai("привет", []))

    assert "регион" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Лимиты: минутный vs дневной, ожидание, ротация ключей
# ---------------------------------------------------------------------------

_DAILY = (
    "429 RESOURCE_EXHAUSTED. quotaId: "
    "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
)


@pytest.mark.parametrize(
    "message",
    [_DAILY, "429 quota exceeded per_day", "429 Daily limit reached"],
)
def test_is_daily_quota_detects_markers(message):
    assert ai._is_daily_quota(Exception(message)) is True


def test_is_daily_quota_false_for_per_minute_limit():
    assert ai._is_daily_quota(Exception("429 RESOURCE_EXHAUSTED. PerMinute")) is False


def test_rate_limit_wait_grows_and_caps(monkeypatch):
    monkeypatch.setattr(ai, "RATE_LIMIT_MAX_WAIT", 60)

    waits = [ai._rate_limit_wait(n) for n in range(1, 7)]

    assert waits == sorted(waits)          # не убывает
    assert max(waits) == 60                # упирается в предел
    assert waits[0] > 0


def test_ask_ai_reports_daily_quota(fake_gemini, one_retry):
    daily = Exception(_DAILY)
    fake_gemini({ai.PRIMARY_MODEL: daily, ai.FALLBACK_MODEL: daily})

    with pytest.raises(ai.AIError) as excinfo:
        asyncio.run(ai.ask_ai("привет", []))

    assert "дневной" in str(excinfo.value).lower()


def test_ask_ai_waits_then_succeeds_on_minute_limit(fake_gemini, monkeypatch):
    """429 без признака дневной квоты: ждём и повторяем, а не падаем сразу."""
    monkeypatch.setattr(ai, "RATE_LIMIT_RETRIES", 3)
    fake = fake_gemini({ai.PRIMARY_MODEL: [Exception("429 PerMinute"), "ответ после ожидания"]})

    assert asyncio.run(ai.ask_ai("привет", [])) == "ответ после ожидания"
    assert len(fake.calls) == 2


def test_ask_ai_rotates_to_next_key_on_quota(monkeypatch):
    """Два ключа: первый исчерпан — берём второй, а не отдаём ошибку."""

    class QuotaModels:
        def generate_content(self, *, model, contents, config=None):
            raise Exception(_DAILY)

        def list(self):
            return []

    class OkModels:
        def generate_content(self, *, model, contents, config=None):
            return SimpleNamespace(text="ответ второго ключа", candidates=[])

        def list(self):
            return []

    class FakeClient2:
        def __init__(self, models):
            self.models = models

    quota_client = FakeClient2(QuotaModels())
    ok_client = FakeClient2(OkModels())
    monkeypatch.setattr(ai, "clients", [quota_client, ok_client])
    monkeypatch.setattr(ai, "client", quota_client)
    monkeypatch.setattr(ai, "_models_checked", True)
    monkeypatch.setattr(ai, "PRIMARY_MODEL", "m")
    monkeypatch.setattr(ai, "FALLBACK_MODEL", "")

    assert asyncio.run(ai.ask_ai("привет", [])) == "ответ второго ключа"


# ---------------------------------------------------------------------------
# _pick_alternative / validate_models
# ---------------------------------------------------------------------------


def test_pick_alternative_follows_preferred_order():
    available = ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite"]

    assert (
        ai._pick_alternative(available, exclude="gemini-2.5-flash")
        == "gemini-2.5-flash-lite"
    )


def test_pick_alternative_falls_back_to_any_flash_model():
    available = ["some-pro-model", "brand-new-flash-2030"]

    assert ai._pick_alternative(available, exclude="") == "brand-new-flash-2030"


def test_pick_alternative_returns_none_when_only_the_excluded_model_exists():
    assert ai._pick_alternative(["gemini-2.5-flash"], exclude="gemini-2.5-flash") is None


# ---------------------------------------------------------------------------
# _pick_available (автоподбор модели, которую ещё не пробовали)
# ---------------------------------------------------------------------------


def test_pick_available_skips_already_tried_models():
    available = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]

    assert ai._pick_available(available, tried={"gemini-2.5-flash"}) == "gemini-2.5-flash-lite"


def test_pick_available_returns_none_when_everything_tried():
    assert ai._pick_available(["gemini-2.5-flash"], tried={"gemini-2.5-flash"}) is None


# ---------------------------------------------------------------------------
# _is_text_model: отсеиваем озвучку/картинки (реальный баг с ...-tts)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "gemini-2.5-flash-preview-tts",
        "gemini-2.5-flash-native-audio-preview-09-2025",
        "gemini-2.5-flash-image",
        "gemini-embedding-001",
        "aqa",
        "deep-research-pro-preview-12-2025",
        "antigravity-preview-latest",
    ],
)
def test_is_text_model_rejects_non_text_models(name):
    assert ai._is_text_model(name) is False


@pytest.mark.parametrize(
    "name", ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro", "some-chat-model"]
)
def test_is_text_model_accepts_chat_models(name):
    assert ai._is_text_model(name) is True


def test_pick_available_never_returns_a_tts_model():
    """Повтор реального сбоя: ключ отдаёт 404 на flash, а автоподбор брал ...-tts."""
    available = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash-preview-tts",
        "gemini-2.5-flash-image",
        "gemini-2.5-pro",
    ]

    picked = ai._pick_available(available, tried={"gemini-2.5-flash", "gemini-2.5-flash-lite"})

    assert picked == "gemini-2.5-pro"


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


# ---------------------------------------------------------------------------
# _status_code (дополнительно)
# ---------------------------------------------------------------------------


def test_status_code_reads_response_attribute():
    class FakeResp:
        status_code = 500

    class FakeExc(Exception):
        code = None
        response = FakeResp()

    assert ai._status_code(FakeExc()) == 500


def test_status_code_reads_code_attribute():
    class FakeExc(Exception):
        code = 400

    assert ai._status_code(FakeExc()) == 400


# ---------------------------------------------------------------------------
# ask_ai: ошибки с конкретными кодами
# ---------------------------------------------------------------------------


def test_ask_ai_404_recommends_model_check(fake_gemini, one_retry):
    fake_gemini({ai.PRIMARY_MODEL: _api_error(404), ai.FALLBACK_MODEL: _api_error(404)})

    with pytest.raises(ai.AIError) as excinfo:
        asyncio.run(ai.ask_ai("привет", []))

    assert "недоступна" in str(excinfo.value).lower()
    assert "GEMINI_MODEL" in str(excinfo.value)


def test_ask_ai_switches_to_available_model_on_404(fake_gemini, one_retry):
    """Обе заданные модели недоступны — берём рабочую из списка ключа."""
    fake = fake_gemini(
        responses={
            ai.PRIMARY_MODEL: _api_error(404),
            ai.FALLBACK_MODEL: _api_error(404),
            "gemini-2.0-flash": "ответ от рабочей модели",
        },
        available=["gemini-2.0-flash"],
    )

    result = asyncio.run(ai.ask_ai("привет", []))

    assert result == "ответ от рабочей модели"
    assert "gemini-2.0-flash" in {call["model"] for call in fake.calls}


# ---------------------------------------------------------------------------
# _remember_working_model (не перебирать 404 на каждом запросе)
# ---------------------------------------------------------------------------


def test_remember_working_model_switches_primary(monkeypatch):
    monkeypatch.setattr(ai, "PRIMARY_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(ai, "FALLBACK_MODEL", "gemini-2.5-flash-lite")

    ai._remember_working_model("gemini-3.8-flash")

    assert ai.PRIMARY_MODEL == "gemini-3.8-flash"
    assert ai.FALLBACK_MODEL == "gemini-2.5-flash"


def test_remember_working_model_keeps_current_when_same(monkeypatch):
    monkeypatch.setattr(ai, "PRIMARY_MODEL", "gemini-3.8-flash")
    monkeypatch.setattr(ai, "FALLBACK_MODEL", "gemini-3.5-flash-lite")

    ai._remember_working_model("gemini-3.8-flash")

    assert ai.PRIMARY_MODEL == "gemini-3.8-flash"
    assert ai.FALLBACK_MODEL == "gemini-3.5-flash-lite"


def test_ask_ai_400_does_not_retry_the_same_model(fake_gemini, monkeypatch):
    """400 — ошибка запроса, крутить её 3 раза бессмысленно."""
    monkeypatch.setattr(ai, "MAX_RETRIES", 3)
    fake = fake_gemini({
        ai.PRIMARY_MODEL: _api_error(400),
        ai.FALLBACK_MODEL: _api_error(400),
    })

    with pytest.raises(ai.AIError):
        asyncio.run(ai.ask_ai("привет", []))

    assert len(fake.calls) == 2  # по одному вызову на каждую модель


def test_ask_ai_403_reports_bad_key(fake_gemini, one_retry):
    fake_gemini({ai.PRIMARY_MODEL: _api_error(403), ai.FALLBACK_MODEL: _api_error(403)})

    with pytest.raises(ai.AIError) as excinfo:
        asyncio.run(ai.ask_ai("привет", []))

    assert "GEMINI_API_KEY" in str(excinfo.value)


# ---------------------------------------------------------------------------
# _check_models_once
# ---------------------------------------------------------------------------


def test_check_models_once_runs_only_once(monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "validate_models", lambda: calls.append(1))
    monkeypatch.setattr(ai, "_models_checked", False)

    asyncio.run(ai._check_models_once())
    asyncio.run(ai._check_models_once())
    asyncio.run(ai._check_models_once())

    assert len(calls) == 1


# ---------------------------------------------------------------------------
# list_available_models
# ---------------------------------------------------------------------------


def test_list_available_models_strips_models_prefix(fake_gemini):
    fake_gemini(available=["gemini-2.5-flash", "gemini-3.5-flash"])

    assert ai.list_available_models() == ["gemini-2.5-flash", "gemini-3.5-flash"]


def test_list_available_models_empty_on_error(monkeypatch):
    class BrokenModels:
        def list(self):
            raise RuntimeError("нет сети")

    monkeypatch.setattr(ai, "client", type("C", (), {"models": BrokenModels()})())

    assert ai.list_available_models() == []


# ---------------------------------------------------------------------------
# _generate_sync edge cases
# ---------------------------------------------------------------------------


def test_generate_sync_empty_text_raises_AIError(fake_gemini):
    """Когда модель вернула None.text, это ошибка."""
    fake_gemini({"m": ""})

    with pytest.raises(ai.AIError) as excinfo:
        ai._generate_sync("m", [])

    assert "finish_reason" in str(excinfo.value).lower() or "не вернула текст" in str(excinfo.value).lower()
