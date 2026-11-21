import json
import logging

import httpx
import pytest

from youtube_summary.telegram import TelegramClient

TOKEN = "123:SECRET"


def _client(handler: httpx.MockTransport) -> TelegramClient:
    return TelegramClient(TOKEN, "42", httpx.AsyncClient(transport=handler))


async def test_send_posts_html_payload() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    assert await _client(httpx.MockTransport(handler)).send("hola") is True
    assert str(seen[0].url).endswith(f"/bot{TOKEN}/sendMessage")
    body = json.loads(seen[0].content)
    assert body["parse_mode"] == "HTML"
    assert body["disable_web_page_preview"] is True
    assert (body["chat_id"], body["text"]) == ("42", "hola")


async def test_send_returns_false_on_non_200(caplog: pytest.LogCaptureFixture) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(400, text="bad " * 200))
    with caplog.at_level(logging.INFO):
        assert await _client(transport).send("x") is False
    assert "400" in caplog.text
    assert "bad " * 75 in caplog.text
    assert "bad " * 76 not in caplog.text


async def test_send_returns_false_when_ok_is_false() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": False}))
    assert await _client(transport).send("x") is False


async def test_send_returns_false_on_transport_error(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed {request.url}")

    with caplog.at_level(logging.DEBUG):
        assert await _client(httpx.MockTransport(handler)).send("x") is False
    assert "ConnectError" in caplog.text
    assert TOKEN not in caplog.text
    assert all(TOKEN not in r.getMessage() for r in caplog.records)


async def test_send_token_never_logged_on_non_200(caplog: pytest.LogCaptureFixture) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(500, text="oops"))
    with caplog.at_level(logging.DEBUG):
        await _client(transport).send("x")
    assert TOKEN not in caplog.text
