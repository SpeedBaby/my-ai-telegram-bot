"""Тесты памяти диалогов (SQLite)."""

import pytest

import memory


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    db_file = tmp_path / "test-memory.db"
    monkeypatch.setattr(memory, "DB_PATH", str(db_file))
    return db_file


def test_history_is_empty_for_a_new_user():
    assert memory.get_history(999) == []


def test_add_and_get_preserves_order_and_roles():
    memory.add_message(1, "user", "первый вопрос")
    memory.add_message(1, "assistant", "первый ответ")
    memory.add_message(1, "user", "второй вопрос")

    assert memory.get_history(1) == [
        ("user", "первый вопрос"),
        ("assistant", "первый ответ"),
        ("user", "второй вопрос"),
    ]


def test_users_do_not_see_each_ones_history():
    memory.add_message(1, "user", "секрет А")
    memory.add_message(2, "user", "секрет Б")

    assert memory.get_history(1) == [("user", "секрет А")]
    assert memory.get_history(2) == [("user", "секрет Б")]


def test_history_is_trimmed_to_the_newest_messages(monkeypatch):
    monkeypatch.setattr(memory, "MAX_MESSAGES", 4)

    for i in range(10):
        memory.add_message(7, "user", f"m{i}")

    assert memory.get_history(7) == [("user", f"m{i}") for i in (6, 7, 8, 9)]


def test_trimming_one_user_does_not_touch_others(monkeypatch):
    monkeypatch.setattr(memory, "MAX_MESSAGES", 2)

    for i in range(5):
        memory.add_message(1, "user", f"a{i}")
    memory.add_message(2, "user", "b0")

    assert len(memory.get_history(1)) == 2
    assert memory.get_history(2) == [("user", "b0")]


def test_clear_history_removes_only_that_user():
    memory.add_message(1, "user", "останется")
    memory.add_message(2, "user", "удалится")

    memory.clear_history(2)

    assert memory.get_history(1) == [("user", "останется")]
    assert memory.get_history(2) == []


def test_clear_history_for_unknown_user_is_safe():
    memory.clear_history(4242)
    assert memory.get_history(4242) == []


def test_unicode_and_multiline_content_survives_roundtrip():
    text = "Привет 🤖\n```python\nprint('код')\n```\n"
    memory.add_message(3, "user", text)

    assert memory.get_history(3) == [("user", text)]


def test_database_file_is_created_on_first_write(isolated_db):
    assert not isolated_db.exists()

    memory.add_message(5, "user", "привет")

    assert isolated_db.exists()
