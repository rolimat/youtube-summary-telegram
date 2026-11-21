import logging

import httpx

logger = logging.getLogger(__name__)
# httpx logs every request URL at INFO, and the Telegram URL embeds the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
_TIMEOUT_SECONDS = 10
_BODY_LOG_CHARS = 300


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, http: httpx.AsyncClient) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._http = http

    async def send(self, text: str) -> bool:
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            response = await self._http.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json=payload,
                timeout=_TIMEOUT_SECONDS,
            )
        except Exception as error:
            message = str(error).replace(self._token, "<redacted>")
            logger.error("telegram send failed: %s: %s", type(error).__name__, message)
            return False
        return self._accepted(response)

    def _accepted(self, response: httpx.Response) -> bool:
        if response.status_code != 200:
            body = response.text[:_BODY_LOG_CHARS].replace(self._token, "<redacted>")
            logger.error("telegram send rejected: %s %s", response.status_code, body)
            return False
        return True
