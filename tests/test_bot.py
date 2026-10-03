"""Тесты bot.py: команды, whitelist, фото, ошибки AI, разбиение длинных ответов.

Вся сеть подменена, тесты работают офлайн.
"""

import asyncio

import pytest

import ai
import bot
import memory


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """Своя база сообщений на каждый тест."""
    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "bot-test.db"))


@pytest.fixture(autouse=True)
def no_typing(monkeypatch):
    """Не дёргать Telegram sendChatAction."""

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bot, "send_typing", _noop)


@pytest.fixture
def sent(monkeypatch):
    """Заглушка send_message: собирает все отправки бота."""
    calls = []

    async def _send(chat_id, text, reply_to=None):
        calls.append({"chat_id": chat_id, "text": text, "reply_to": reply_to})

    monkeypatch.setattr(bot, "send_message", _send)
    return calls


@pytest.fixture
def ai_stub(monkeypatch):
    """Заглушка AI: отвечает фиксированным текстом и пишет, что к нему пришло."""
    received: dict = {}

    async def _ask(*, user_message, history=None, image_bytes=None, image_mime=None):
        received["user_message"] = user_message
        received["history"] = history or []
        received["image_bytes"] = image_bytes
        received["image_mime"] = image_mime
        return "ответ модели"

    monkeypatch.setattr(bot, "ask_ai", _ask)
    return received


def _text_message(text, *, chat_id=100, user_id=100, message_id=7):
    return {
        "update_id": 1,
        "message": {
            "message_id": message_id,
            "from": {"id": user_id},
            "chat": {"id": chat_id},
            "text": text,
        },
    }


def _photo_message(caption, *, photo_sizes=(20, 80, 200), user_id=100):
    return {
        "update_id": 2,
        "message": {
            "message_id": 8,
            "from": {"id": user_id},
            "chat": {"id": user_id},
            "caption": caption,
            "photo": [
                {"file_id": f"id-{s}", "file_size": s} for s in photo_sizes
            ],
        },
    }


# ---------------------------------------------------------------------------
# _split_text
# ---------------------------------------------------------------------------


def test_split_text_keeps_short_message_single():
    assert bot._split_text("короткий ответ", 100) == ["короткий ответ"]


def test_split_text_never_exceeds_the_limit():
    text = "\n\n".join(["абзац " * 30] * 10)

    chunks = bot._split_text(text, 200)

    assert all(len(chunk) <= 200 for chunk in chunks)


def test_split_text_keeps_all_paragraphs_in_order():
    text = "\n\n".join(f"абзац номер {i}" for i in range(6))

    chunks = bot._split_text(text, 40)

    assert len(chunks) > 1
    assert "\n\n".join(chunks) == text


def test_split_text_hard_splits_one_huge_paragraph():
    text = "x" * 500

    chunks = bot._split_text(text, 120)

    assert "".join(chunks) == text


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------


def test_start_describes_capabilities(sent):
    asyncio.run(bot.process_update(_text_message("/start")))

    text = sent[0]["text"]
    assert "AI-бот" in text
    for command in ("/start", "/reset", "/id", "/about"):
        assert command in text


def test_start_works_with_bot_name_suffix(sent):
    asyncio.run(bot.process_update(_text_message("/start@MyBot")))

    assert "AI-бот" in sent[0]["text"]


def test_reset_clears_history_of_this_user(sent):
    memory.add_message(100, "user", "старый вопрос")

    asyncio.run(bot.process_update(_text_message("/reset")))

    assert memory.get_history(100) == []
    assert "очищена" in sent[0]["text"]


def test_id_returns_numeric_user_id(sent):
    asyncio.run(bot.process_update(_text_message("/id")))

    assert "100" in sent[0]["text"]


def test_unknown_command_is_rejected(sent):
    asyncio.run(bot.process_update(_text_message("/weather")))

    assert "/start" in sent[0]["text"]


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------


def test_foreign_user_is_blocked_when_whitelist_set(sent, monkeypatch, ai_stub):
    monkeypatch.setattr(bot, "ALLOWED_USERS", {1})

    asyncio.run(bot.process_update(_text_message("привет", user_id=2)))

    assert "приватный" in sent[0]["text"]
    assert ai_stub == {}  # AI вообще не вызывался


def test_allowed_user_gets_answer(sent, ai_stub):
    asyncio.run(bot.process_update(_text_message("привет", user_id=100)))

    assert sent[0]["text"] == "ответ модели"


# ---------------------------------------------------------------------------
# Текст: ответ + память
# ---------------------------------------------------------------------------


def test_text_question_is_answered_and_stored(sent, ai_stub):
    asyncio.run(bot.process_update(_text_message("сколько будет 2+2?")))

    assert ai_stub["user_message"] == "сколько будет 2+2?"
    assert sent[0]["text"] == "ответ модели"
    assert sent[0]["reply_to"] == 7
    assert memory.get_history(100) == [
        ("user", "сколько будет 2+2?"),
        ("assistant", "ответ модели"),
    ]


def test_previous_answers_are_sent_as_history(sent, ai_stub):
    memory.add_message(100, "user", "прошлый вопрос")
    memory.add_message(100, "assistant", "прошлый ответ")

    asyncio.run(bot.process_update(_text_message("новый вопрос")))

    assert ("user", "прошлый вопрос") in ai_stub["history"]


def test_too_long_message_is_rejected(sent, ai_stub):
    asyncio.run(bot.process_update(_text_message("a" * 12_001)))

    assert "слишком большое" in sent[0]["text"].lower()
    assert ai_stub == {}


def test_empty_message_is_ignored(sent, ai_stub):
    asyncio.run(bot.process_update(_text_message("   ")))

    assert sent == []
    assert ai_stub == {}


def test_update_without_message_is_ignored(sent):
    asyncio.run(bot.process_update({"update_id": 3}))

    assert sent == []


def test_edited_message_is_handled(sent, ai_stub):
    update = _text_message("исправленный текст")
    update["edited_message"] = update.pop("message")

    asyncio.run(bot.process_update(update))

    assert ai_stub["user_message"] == "исправленный текст"


# ---------------------------------------------------------------------------
# Фотографии
# ---------------------------------------------------------------------------


def test_photo_is_downloaded_and_sent_to_ai(sent, ai_stub, monkeypatch):
    requested: list[str] = []

    async def fake_download(file_id):
        requested.append(file_id)
        return b"PNGDATA", "image/png"

    monkeypatch.setattr(bot, "download_telegram_file", fake_download)

    asyncio.run(bot.process_update(_photo_message("что на фото?")))

    assert requested == ["id-200"]
    assert ai_stub["image_bytes"] == b"PNGDATA"
    assert ai_stub["image_mime"] == "image/png"
    assert ai_stub["user_message"] == "что на фото?"


def test_photo_without_caption_gets_default_prompt(sent, ai_stub, monkeypatch):
    async def fake_download(file_id):
        return b"PNGDATA", "image/png"

    monkeypatch.setattr(bot, "download_telegram_file", fake_download)

    asyncio.run(bot.process_update(_photo_message("")))

    assert "проанализируй" in ai_stub["user_message"].lower()


def test_largest_photo_size_is_used(sent, ai_stub, monkeypatch):
    seen = []

    async def fake_download(file_id):
        seen.append(file_id)
        return b"x", "image/jpeg"

    monkeypatch.setattr(bot, "download_telegram_file", fake_download)

    asyncio.run(bot.process_update(_photo_message("описание")))

    assert seen == ["id-200"]


def test_photo_is_marked_in_history(sent, ai_stub, monkeypatch):
    async def fake_download(file_id):
        return b"x", "image/jpeg"

    monkeypatch.setattr(bot, "download_telegram_file", fake_download)

    asyncio.run(bot.process_update(_photo_message("что это?")))

    assert memory.get_history(100)[0][0] == "user"
    assert "[Фотография]" in memory.get_history(100)[0][1]


def test_photo_download_failure_is_reported(sent, monkeypatch):
    async def broken_download(file_id):
        raise RuntimeError("network down")

    monkeypatch.setattr(bot, "download_telegram_file", broken_download)

    asyncio.run(bot.process_update(_photo_message("что на фото?")))

    assert "не удалось скачать" in sent[0]["text"].lower()


def test_document_is_politely_refused(sent):
    update = _text_message("")
    update["message"]["document"] = {"file_name": "курс.pdf"}

    asyncio.run(bot.process_update(update))

    assert "только фотографии" in sent[0]["text"]


# ---------------------------------------------------------------------------
# Ошибки AI
# ---------------------------------------------------------------------------


def test_ai_error_message_is_shown_to_user(sent, monkeypatch):
    async def failing_ask(**_kwargs):
        raise ai.AIError("Бесплатный лимит запросов исчерпан.")

    monkeypatch.setattr(bot, "ask_ai", failing_ask)

    asyncio.run(bot.process_update(_text_message("привет")))

    assert "лимит" in sent[0]["text"]


def test_unexpected_ai_error_does_not_crash(sent, monkeypatch):
    async def exploding_ask(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot, "ask_ai", exploding_ask)

    asyncio.run(bot.process_update(_text_message("привет")))

    assert sent and "позже" in sent[0]["text"]


def test_failed_answer_is_not_saved_to_memory(sent, monkeypatch):
    async def failing_ask(**_kwargs):
        raise ai.AIError("oops")

    monkeypatch.setattr(bot, "ask_ai", failing_ask)

    asyncio.run(bot.process_update(_text_message("вопрос")))

    assert memory.get_history(100) == []


# ---------------------------------------------------------------------------
# Дополнительные проверки: фото, ответы, сплит
# ---------------------------------------------------------------------------


def test_photo_too_large_is_rejected(sent, ai_stub, monkeypatch):
    """Фото > 10 МБ отклоняется без скачивания."""

    async def _never_download(file_id):
        raise RuntimeError("should not reach download")

    monkeypatch.setattr(bot, "download_telegram_file", _never_download)

    asyncio.run(
        bot.process_update(
            _photo_message("проверка", photo_sizes=(20, 80, 15_000_000))
        )
    )

    assert "слишком большая" in sent[0]["text"]
    assert ai_stub == {}


def test_photo_download_failure_sent_to_user(sent, monkeypatch):
    async def _broken(file_id):
        raise RuntimeError("network down")

    monkeypatch.setattr(bot, "download_telegram_file", _broken)

    asyncio.run(bot.process_update(_photo_message("что на фото?")))

    assert "не удалось скачать" in sent[0]["text"].lower()


def test_answer_and_reply_handles_ai_error(sent, monkeypatch):
    """Когда ask_ai бросает AIError, пользователь получает сообщение об ошибке."""

    async def _failing_ask(**_kwargs):
        raise ai.AIError("лимит исчерпан")

    monkeypatch.setattr(bot, "ask_ai", _failing_ask)

    asyncio.run(
        bot.answer_and_reply(100, 100, "вопрос", "вопрос", reply_to=42)
    )

    assert "лимит" in sent[0]["text"]
    assert sent[0]["reply_to"] == 42
    assert memory.get_history(100) == []


def test_answer_and_reply_handles_generic_error(sent, monkeypatch):
    """Когда ask_ai бросает RuntimeError, пользователь получает сообщение."""

    async def _failing_ask(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot, "ask_ai", _failing_ask)

    asyncio.run(
        bot.answer_and_reply(100, 100, "вопрос", "вопрос")
    )

    assert "ошибка" in sent[0]["text"].lower()
    assert memory.get_history(100) == []


def test_split_text_empty_string():
    assert bot._split_text("", 100) == [""]


def test_split_text_single_paragraph_no_break():
    text = "a" * 50
    chunks = bot._split_text(text, 100)
    assert chunks == [text]
