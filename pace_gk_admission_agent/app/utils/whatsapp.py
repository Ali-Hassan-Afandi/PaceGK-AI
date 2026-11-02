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


def _mask_phone_number(phone_number: str) -> str:
    """Return a masked rendering of a phone number for safe logging.

    Only the last four digits are exposed. Never log full student phone
    numbers (personal information) in log output.
    """
    digits = "".join(ch for ch in phone_number if ch.isdigit())
    if not digits:
        return ""
    return "***" + digits[-4:]


def _safe_endpoint(url: str) -> str:
    """Return the request endpoint safe for logging (no credentials).

    The WhatsApp Cloud API messages endpoint carries no credentials in the URL
    itself; the bearer token lives in the Authorization header, which must
    never be logged. The URL is therefore safe to log as-is.
    """
    return url


def _extract_meta_error(response: httpx.Response) -> dict:
    """Safely extract the Meta Graph API error object from an error response.

    Meta returns errors shaped like:
        {"error": {"code": 123, "message": "...", "type": "OAuthException",
                   "error_subcode": 456, "error_data": {"details": {...}},
                   "fbtrace_id": "..."}}
    Returns {} when the body cannot be parsed or holds no error object.
    Never returns credentials.
    """
    try:
        data = response.json()
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    error = data.get("error")
    return error if isinstance(error, dict) else {}


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
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            meta_error = _extract_meta_error(exc.response)
            error_data = meta_error.get("error_data")
            error_data_details = (
                error_data.get("details") if isinstance(error_data, dict) else None
            )
            logger.error(
                "WhatsApp Cloud API send failed. "
                "status=%s endpoint=%s phone_number_id=%s "
                "meta_error_code=%s meta_error_message=%s meta_error_type=%s "
                "meta_error_subcode=%s meta_error_data_details=%s meta_error_fbtrace_id=%s",
                exc.response.status_code,
                _safe_endpoint(url),
                phone_number_id,
                meta_error.get("code"),
                meta_error.get("message"),
                meta_error.get("type"),
                meta_error.get("error_subcode"),
                error_data_details,
                meta_error.get("fbtrace_id"),
            )
            # Do not suppress: propagate the error so callers can see the failure.
            raise
        data = response.json()
        logger.info(
            "WhatsApp API accepted the message. to=%s, response=%s",
            _mask_phone_number(recipient),
            _safe_log_response(data),
        )
        return data


def _safe_log_response(data: dict) -> dict:
    """Return a safe subset of the API response for logging (no secrets, no personal info).

    ``contacts`` is dropped because it contains the recipient's phone number
    (``input`` / ``wa_id``), which is personal information and must not be
    logged. Message ids/types carry no personal data.
    """
    if not isinstance(data, dict):
        return {}
    safe = {}
    if "messaging_product" in data:
        safe["messaging_product"] = data["messaging_product"]
    messages = data.get("messages")
    if isinstance(messages, list):
        safe["messages"] = [
            {key: value for key, value in message.items() if key in ("id", "type")}
            if isinstance(message, dict)
            else message
            for message in messages
        ]
    return safe
