import httpx

from ..config import settings


async def send_whatsapp_text(to_number: str, body: str) -> dict:
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        # Local dev / testing fallback when WhatsApp credentials are not configured yet.
        print(f"[MOCK] WhatsApp message -> {to_number}: {body}")
        return {"sid": "mock-message", "to": to_number, "body": body}

    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
    )
    data = {
        "From": settings.whatsapp_from_number,
        "To": to_number,
        "Body": body,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            data=data,
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        )
        response.raise_for_status()
        return response.json()
