"""Optional Telegram alerts using the operator's own bot token."""

from __future__ import annotations

import httpx


async def send_telegram(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(url, json={"chat_id": chat_id, "text": text})
        return r.status_code == 200
