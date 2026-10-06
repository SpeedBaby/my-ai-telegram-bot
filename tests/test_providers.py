"""Tests for the fallback provider module (providers.py).

No real network calls: httpx.AsyncClient is replaced with a fake.
"""

import asyncio
import base64
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

import providers


class _FakeResponse:
    def __init__(self, payload=None, status=200, text=""):
        self._payload = payload if payload is not None else {}
        self.status_code = status
        self.text = text or json.dumps(self._payload)

    def json(self):
        if self._payload is None:
            raise json.JSONDecodeError("no json", "", 0)
        return self._payload


class _FakeClient:
    """Substitute for httpx.AsyncClient with a prepared response or exception."""

    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self._exc is not None:
            raise self._exc
        return self._response


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    """A fake key so is_configured() passes in the tests."""
    monkeypatch.setattr(providers, "GROQ_API_KEY", "gsk-test-key")


def _install_client(monkeypatch, response=None, exc=None):
    fake = _FakeClient(response=response, exc=exc)
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: fake)
    return fake


def _ask(**kwargs):
    """Run providers.ask with sane defaults."""
    params = {
        "history": [],
        "user_message": "Как дела?",
        "system_prompt": "система",
    }
    params.update(kwargs)
    return asyncio.run(providers.ask(**params))


# --- configuration -----------------------------------------------------------

def test_is_configured_requires_key(monkeypatch):
    monkeypatch.setattr(providers, "GROQ_API_KEY", "")
    assert providers.is_configured() is False
    monkeypatch.setattr(providers, "GROQ_API_KEY", "key")
    assert providers.is_configured() is True


def test_ask_without_key_raises(monkeypatch):
    monkeypatch.setattr(providers, "GROQ_API_KEY", "")
    with pytest.raises(providers.ProviderError):
        _ask()


# --- message building --------------------------------------------------------

def test_build_messages_puts_system_first_and_user_last():
    messages = providers._build_messages(
        history=[("user", "привет"), ("assistant", "здравствуйте")],
        user_message="вопрос",
        system_prompt="система",
    )

    assert messages[0] == {"role": "system", "content": "система"}
    assert messages[1] == {"role": "user", "content": "привет"}
    assert messages[2] == {"role": "assistant", "content": "здравствуйте"}
    assert messages[-1] == {"role": "user", "content": "вопрос"}


def test_build_messages_with_image_uses_data_url():
    image = b"\x89PNG fake"
    messages = providers._build_messages(
        history=[],
        user_message="что на фото?",
        system_prompt="система",
        image_bytes=image,
        image_mime="image/png",
    )

    content = messages[-1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "что на фото?"}
    expected = base64.b64encode(image).decode("ascii")
    assert content[1]["image_url"]["url"] == f"data:image/png;base64,{expected}"


# --- request -----------------------------------------------------------------

def test_ask_returns_answer_and_sends_correct_request(monkeypatch):
    response = _FakeResponse({"choices": [{"message": {"content": "Привет!"}}]})
    client = _install_client(monkeypatch, response=response)

    answer = _ask()

    assert answer == "Привет!"
    url, kwargs = client.calls[0]
    assert url == f"{providers.GROQ_BASE_URL}/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer gsk-test-key"
    assert kwargs["json"]["model"] == providers.GROQ_MODEL
    assert kwargs["json"]["messages"][0]["content"] == "система"


def test_ask_uses_vision_model_for_images(monkeypatch):
    response = _FakeResponse({"choices": [{"message": {"content": "Кот"}}]})
    client = _install_client(monkeypatch, response=response)

    _ask(image_bytes=b"img", image_mime="image/jpeg")

    assert client.calls[0][1]["json"]["model"] == providers.GROQ_VISION_MODEL


def test_ask_strips_whitespace(monkeypatch):
    _install_client(
        monkeypatch,
        response=_FakeResponse({"choices": [{"message": {"content": "  ок  "}}]}),
    )

    assert _ask() == "ок"


def test_ask_reports_http_error(monkeypatch):
    _install_client(monkeypatch, response=_FakeResponse(status=429, text="quota"))

    with pytest.raises(providers.ProviderError, match="429"):
        _ask()


def test_ask_reports_network_error(monkeypatch):
    _install_client(monkeypatch, exc=httpx.ConnectError("no route"))

    with pytest.raises(providers.ProviderError, match="Сеть"):
        _ask()


def test_ask_reports_timeout(monkeypatch):
    _install_client(monkeypatch, exc=httpx.ReadTimeout("slow"))

    with pytest.raises(providers.ProviderError):
        _ask()


def test_ask_reports_unexpected_format(monkeypatch):
    _install_client(monkeypatch, response=_FakeResponse({"unexpected": True}))

    with pytest.raises(providers.ProviderError, match="формат"):
        _ask()


def test_ask_reports_empty_answer(monkeypatch):
    _install_client(
        monkeypatch,
        response=_FakeResponse({"choices": [{"message": {"content": "   "}}]}),
    )

    with pytest.raises(providers.ProviderError, match="пустой"):
        _ask()


# --- request over a real socket (nothing is monkeypatched inside providers) ---


class _ChatServer(BaseHTTPRequestHandler):
    """Отвечает как OpenAI-совместимый API и запоминает входящие запросы."""

    requests: list = []
    finish_reason = "stop"

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _ChatServer.requests.append(
            {"payload": payload, "auth": self.headers.get("Authorization")}
        )
        body = json.dumps(
            {
                "choices": [
                    {
                        "message": {"content": f"ответ от {payload['model']}"},
                        "finish_reason": _ChatServer.finish_reason,
                    }
                ]
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        """Не печатать служебные строки сервера в вывод тестов."""


@pytest.fixture
def groq_server(monkeypatch):
    """Реальный HTTP-сервер на свободном порту вместо api.groq.com."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ChatServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(
        providers, "GROQ_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}"
    )
    _ChatServer.requests = []
    _ChatServer.finish_reason = "stop"
    yield
    server.shutdown()
    server.server_close()


def test_real_request_sends_bearer_key_and_model(groq_server):
    answer = _ask(user_message="Привет!")

    assert answer == f"ответ от {providers.GROQ_MODEL}"
    sent = _ChatServer.requests[-1]
    assert sent["auth"] == "Bearer gsk-test-key"
    assert sent["payload"]["model"] == providers.GROQ_MODEL
    assert sent["payload"]["max_tokens"] == providers._MAX_OUTPUT_TOKENS


def test_vision_model_is_used_for_images(groq_server):
    _ask(user_message="что на фото?", image_bytes=b"img", image_mime="image/jpeg")

    assert _ChatServer.requests[-1]["payload"]["model"] == providers.GROQ_VISION_MODEL


def test_history_is_sent_in_order(groq_server):
    _ask(history=[("user", "первый"), ("assistant", "второй")], user_message="третий")

    roles = [m["role"] for m in _ChatServer.requests[-1]["payload"]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_truncated_answer_is_returned_but_logged(groq_server, caplog):
    """Ответ упёрся в max_tokens: отдаём что есть, но пишем warning в лог."""
    _ChatServer.finish_reason = "length"

    with caplog.at_level(logging.WARNING, logger="providers"):
        answer = _ask()

    assert answer == f"ответ от {providers.GROQ_MODEL}"
    assert any("обрезал" in record.getMessage() for record in caplog.records)
