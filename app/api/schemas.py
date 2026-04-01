from pydantic import BaseModel
from typing import Any, Literal


class ChatRequest(BaseModel):
    message: str
    provider: Literal["claude", "openai", "ollama"] | None = None
    session_id: str | None = "default"
    tts: bool = False


class ChatResponse(BaseModel):
    source: str
    intent: str | None = None
    provider: str | None = None
    response: Any
    tools_used: list[str] | None = None
    input_source: str | None = None
    session_id: str | None = None
    rag_used: bool | None = None
    pipeline: dict | None = None
    tts: dict | None = None


class RAGIndexResponse(BaseModel):
    indexed_consultas: int
    message: str
