import logging
from typing import Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v26.0"


def _normalize_phone_number(phone_number: str) -> str:
    """Normalize a phone number to international digit format for Meta WhatsApp Cloud API.

    Removes 'whatsapp:' prefix, '+', spaces, parentheses, and hyphens.
    Preserves the country code.
    """
    number = phone_number.strip()
    if number.startswith("whatsapp:"):
        number = number[len("whatsapp:"):]
    number = (
        number.replace("+", "")
        .replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
    )
    return number


async def send_whatsapp_text(to_number: str, body: str) -> dict:
    """Send a text message via Meta WhatsApp Cloud API.

    Returns the Meta API response dict (includes 'messages' with 'id' / wamid).
    Raises httpx.HTTPStatusError on non-2xx responses.
    Never logs access tokens or verify tokens.
    """
    phone_number_id = settings.whatsapp_phone_number_id
    access_token = settings.whatsapp_access_token

    if not phone_number_id or not access_token:
        logger.error(
            "WhatsApp Cloud API credentials not configured. "
            "Set WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_ACCESS_TOKEN environment variables."
        )
        return {
            "error": "whatsapp_credentials_not_configured",
            "to": to_number,
            "body": body,
        }

    recipient = _normalize_phone_number(to_number)
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {
            "body": body,
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        logger.info(
            "WhatsApp API accepted the message. to=%s, response=%s",
            recipient,
            _safe_log_response(data),
        )
        return data


def _safe_log_response(data: dict) -> dict:
    """Return a safe subset of the API response for logging (no secrets)."""
    if not isinstance(data, dict):
        return {}
    safe = {}
    for key in ("messaging_product", "contacts", "messages"):
        if key in data:
            safe[key] = data[key]
    return safe
