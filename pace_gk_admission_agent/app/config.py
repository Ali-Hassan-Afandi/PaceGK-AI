from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # WhatsApp (Twilio)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    whatsapp_from_number: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_verify_token: str = ""

    # LLM
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    # Database
    database_url: str = "sqlite:///./pace_gk_admission_agent.db"

    # Admission batch data-sheet output directory (one CSV per batch).
    admission_export_dir: str = "admission_data"

    # Admin / approval numbers (WhatsApp). Includes the owner's test number 03327041010.
    admin_numbers: List[str] = ["03367483185", "03463156495", "03327041010"]

    model_config = {"env_file": ".env"}


settings = Settings()
