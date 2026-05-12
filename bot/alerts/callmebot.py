"""
bot/alerts/callmebot.py — WhatsApp alerts via CallMeBot API.

Used ONLY for critical alerts (capital floor hit, system crash).
Free service — no app install needed, just WhatsApp + API key from callmebot.com.
"""
from __future__ import annotations

import logging
import urllib.parse

import httpx

log = logging.getLogger("nanorca.alerts.callmebot")

CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"


class CallMeBot:
    """Sends WhatsApp messages via the CallMeBot free API."""

    def __init__(self, config) -> None:
        self._phone = config.callmebot_phone
        self._api_key = config.callmebot_api_key

    @property
    def _configured(self) -> bool:
        return bool(self._phone and self._api_key)

    async def send_critical(self, message: str) -> bool:
        """
        Send a critical WhatsApp alert.

        Returns True on success. Does not raise — logs errors instead.
        """
        if not self._configured:
            log.warning("CallMeBot not configured — skipping WhatsApp alert")
            return False

        encoded = urllib.parse.quote(message)
        url = f"{CALLMEBOT_URL}?phone={self._phone}&text={encoded}&apikey={self._api_key}"

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    log.info("CallMeBot WhatsApp alert sent")
                    return True
                else:
                    log.error(f"CallMeBot error: HTTP {resp.status_code} — {resp.text[:200]}")
                    return False
        except httpx.HTTPError as e:
            log.error(f"CallMeBot request failed: {e}")
            return False
