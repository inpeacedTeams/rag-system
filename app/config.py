import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    chat_model: str = os.getenv("CHAT_MODEL", "gpt-4.1-mini")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))
    qdrant_url: str = os.getenv("QDRANT_URL", "http://qdrant:6333").rstrip("/")
    qdrant_api_key: str = os.getenv("QDRANT_API_KEY", "")
    collection: str = os.getenv("QDRANT_COLLECTION", "documents")
    session_secret: str = os.getenv("SESSION_SECRET", "")
    secure_cookie: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    min_score: float = float(os.getenv("MIN_RELEVANCE_SCORE", "0.35"))
    max_upload_bytes: int = 20 * 1024 * 1024
    max_site_bytes: int = 5 * 1024 * 1024
    max_chunks: int = 1000
    max_documents: int = 30
    max_pages: int = 300
    top_k: int = 8


settings = Settings()
