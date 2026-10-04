"""
Общие фикстуры тестов.

Важно: переменные окружения выставляются ДО импорта модулей приложения —
ai.py и bot.py читают GEMINI_API_KEY / BOT_TOKEN прямо при импорте.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")
os.environ.setdefault("WEBHOOK_SECRET", "test-secret")

import pytest  # noqa: E402

import ai  # noqa: E402


class FakeModels:
    """Замена client.models: возвращает нужные ответы и имитирует ошибки."""

    def __init__(self, responses=None, available=None):
        # responses: {имя_модели: текст | исключение | [элементы по порядку]}
        self.responses = {
            name: (queue if isinstance(queue, list) else [queue])
            for name, queue in (responses or {}).items()
        }
        self.available = available
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        queue = self.responses.get(model)
        if queue is None:
            return SimpleNamespace(text="ответ модели", candidates=[])
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(text=item, candidates=[])

    def list(self):
        if self.available is None:
            raise RuntimeError("model list unavailable")
        return [SimpleNamespace(name=f"models/{name}") for name in self.available]


class FakeClient:
    def __init__(self, models: FakeModels) -> None:
        self.models = models


@pytest.fixture
def fake_gemini(monkeypatch):
    """Ставит поддельный Gemini-клиент. Возвращает объект с записанными вызовами.

    models = fake_gemini({"gemini-3.8-flash": "привет"})
    """

    def install(responses=None, available=None) -> FakeModels:
        fake = FakeModels(responses=responses, available=available)
        fake_client = FakeClient(fake)
        monkeypatch.setattr(ai, "client", fake_client)
        # ask_ai перебирает ключи из ai.clients — подменяем и его.
        monkeypatch.setattr(ai, "clients", [fake_client])
        # Валидация имён моделей при первом запросе не нужна в тестах.
        monkeypatch.setattr(ai, "_models_checked", True)
        return fake

    return install


@pytest.fixture
def one_retry(monkeypatch):
    """Не ждать по 2/4/6 секунд в тестах с ретраями."""
    monkeypatch.setattr(ai, "MAX_RETRIES", 1)
