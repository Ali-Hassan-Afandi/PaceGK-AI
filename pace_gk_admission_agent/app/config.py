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

    # --- Persistent image storage -------------------------------------------
    # Provider: "local" (dev/test only, filesystem) or "supabase".
    storage_provider: str = "local"
    # Local storage path (only used when storage_provider == "local").
    storage_local_path: str = "admission_storage"
    # Supabase Storage (production). Required when storage_provider == "supabase".
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_storage_bucket: str = ""

    # --- Knowledge base / RAG ------------------------------------------------
    # Raw source folders the knowledge base is built from. The app lives in the
    # `pace_gk_admission_agent/` subfolder while the docs sit at the repo root,
    # so the defaults point one level up from the app directory.
    knowledge_dir: str = "../DataDocs"
    admission_docs_dir: str = "../AdmissionDocs"
    # How many chunks to retrieve per question.
    knowledge_top_k: int = 3
    # Embedding settings (OpenAI-compatible: OpenAI or Groq).
    knowledge_embedding_model: str = "text-embedding-3-small"
    knowledge_embedding_api_key: str = ""
    knowledge_embedding_base_url: str = "https://api.openai.com/v1"

    model_config = {"env_file": ".env"}


settings = Settings()
