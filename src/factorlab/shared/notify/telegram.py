"""TelegramBackend — push alert to a chat (lifted from india_equities_upstox_premarket).

Required env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
"""

from __future__ import annotations

import logging
import os

import requests

from factorlab.shared.notify._backend import Backend, Notification

log = logging.getLogger(__name__)

_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramBackend(Backend):
    """Send via Telegram Bot API."""

    def send(self, notification: Notification) -> bool:
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not (bot_token and chat_id):
            log.warning(
                "[notify:telegram] missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
                "-- skipping"
            )
            return False

        text = f"{notification.subject_with_tag()}\n\n{notification.body}"
        try:
            resp = requests.post(
                _SEND_URL.format(token=bot_token),
                json={"chat_id": chat_id, "text": text},
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            log.error("[notify:telegram] send failed: %s", e)
            return False


__all__ = ["TelegramBackend"]
