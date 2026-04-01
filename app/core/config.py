from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Medical RAG Agent — San Marcos"
    app_env: str = "development"
    debug: bool = True
    api_prefix: str = "/api"
    host: str = "0.0.0.0"
    port: int = 8000

    # ── Base de datos MySQL (IP pública) ─────────────────────────────
    db_host: str = "localhost"
    db_port: int = 3306
    db_name: str = "sanmarcosguayaquil"
    db_user: str = "root"
    db_password: str = "changeme"

    # ── LLM: "claude" | "openai" | "ollama" ─────────────────────────
    llm_provider: str = "claude"
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"

    # ── RAG ──────────────────────────────────────────────────────────
    rag_chunk_size: int = 500
    rag_chunk_overlap: int = 50
    rag_top_k: int = 5

    # ── Audio STT: "browser" | "whisper" | "google" ──────────────────
    stt_provider: str = "browser"
    whisper_model: str = "base"
    google_speech_key: str = ""

    # ── Audio TTS: "browser" | "openai" | "google" ───────────────────
    tts_provider: str = "browser"
    tts_voice: str = "es-EC"
    enable_tts: bool = True

    # ── Sesiones ─────────────────────────────────────────────────────
    session_max_history: int = 20

    # ── Langfuse observabilidad ──────────────────────────────────────
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


settings = Settings()
