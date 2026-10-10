import os
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    kiro_api_key: str = os.getenv("KIRO_API_KEY", "")
    # Legacy key/base variables remain accepted, so existing deployments can migrate explicitly.
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str = os.getenv("CHAT_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://api.xkiro.com/v1")).rstrip("/")
    chat_model: str = os.getenv("CHAT_MODEL", "qwen/qwen3.8-omni-flash:free")
    chat_response_format: str = os.getenv("CHAT_RESPONSE_FORMAT", "prompt")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", "384"))
    embedding_cache_dir: str = os.getenv("EMBEDDING_CACHE_DIR", "/models")
    embedding_threads: int = int(os.getenv("EMBEDDING_THREADS", "2"))
    qdrant_url: str = os.getenv("QDRANT_URL", "http://qdrant:6333").rstrip("/")
    qdrant_api_key: str = os.getenv("QDRANT_API_KEY", "")
    collection: str = os.getenv("QDRANT_COLLECTION", "documents_kiro_minilm_v1")
    session_secret: str = os.getenv("SESSION_SECRET", "")
    secure_cookie: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    min_score: float = float(os.getenv("MIN_RELEVANCE_SCORE", "0.35"))
    max_upload_bytes: int = 20 * 1024 * 1024
    max_site_bytes: int = 5 * 1024 * 1024
    max_chunks: int = 1000
    max_documents: int = 30
    max_pages: int = 300
    top_k: int = 8

    @property
    def chat_api_key(self) -> str:
        # Never send an old OpenAI key to Kiro merely because the default URL changed.
        if urlsplit(self.openai_base_url).hostname == 'api.xkiro.com':
            return self.kiro_api_key
        return self.openai_api_key or self.kiro_api_key


settings = Settings()
