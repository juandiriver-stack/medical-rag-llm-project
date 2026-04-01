"""
LLM Factory — Claude, OpenAI/ChatGPT, Ollama.
Todos los errores de red, autenticación y timeout se capturan en invoke()
y lanzan un ValueError con mensaje claro, para que los agentes activen
el fallback por reglas sin romper el pipeline.
"""
from typing import Protocol
import httpx
from app.core.config import settings


class LLMClient(Protocol):
    def invoke(self, prompt: str, system: str = "") -> str: ...
    @property
    def provider_name(self) -> str: ...


# ── NoLLM: placeholder cuando no hay proveedor disponible ─────────────
class NoLLM:
    """Se usa cuando no hay API key ni Ollama disponible."""
    provider_name = "none"

    def invoke(self, prompt: str, system: str = "") -> str:
        raise ValueError("Sin proveedor LLM configurado")


# ── Claude ────────────────────────────────────────────────────────────
class ClaudeWrapper:
    provider_name = "claude"

    def __init__(self) -> None:
        import anthropic
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def invoke(self, prompt: str, system: str = "") -> str:
        try:
            import anthropic
            kwargs = {
                "model": settings.claude_model,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system
            msg = self._client.messages.create(**kwargs)
            return msg.content[0].text
        except Exception as e:
            raise ValueError(f"Claude error: {e}") from e


# ── OpenAI ────────────────────────────────────────────────────────────
class OpenAIWrapper:
    provider_name = "openai"

    def __init__(self) -> None:
        from langchain_openai import ChatOpenAI
        self._client = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=0,
        )

    def invoke(self, prompt: str, system: str = "") -> str:
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            messages = []
            if system:
                messages.append(SystemMessage(content=system))
            messages.append(HumanMessage(content=prompt))
            response = self._client.invoke(messages)
            return response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            raise ValueError(f"OpenAI error: {e}") from e


# ── Ollama ────────────────────────────────────────────────────────────
class OllamaWrapper:
    provider_name = "ollama"

    def invoke(self, prompt: str, system: str = "") -> str:
        try:
            full_prompt = f"{system}\n\n{prompt}" if system else prompt
            payload = {
                "model": settings.ollama_model,
                "prompt": full_prompt,
                "stream": False,
            }
            response = httpx.post(
                f"{settings.ollama_base_url}/api/generate",
                json=payload,
                timeout=120.0,
            )
            response.raise_for_status()
            return response.json().get("response", "")
        except Exception as e:
            raise ValueError(f"Ollama error: {e}") from e


# ── Factory ───────────────────────────────────────────────────────────
def _key_valida(key: str) -> bool:
    """Una key es válida si existe, no está vacía y no es el placeholder."""
    return bool(key and key.strip() and not key.startswith("<") and len(key) > 8)


def get_llm(provider: str | None = None) -> LLMClient:
    """
    Retorna el cliente LLM apropiado.
    Si no hay key válida para el proveedor solicitado, devuelve NoLLM()
    en lugar de crear un cliente que fallará en runtime.
    """
    p = (provider or settings.llm_provider or "").lower().strip()

    if p == "claude":
        if _key_valida(settings.anthropic_api_key):
            return ClaudeWrapper()
        return NoLLM()

    if p == "openai":
        if _key_valida(settings.openai_api_key):
            return OpenAIWrapper()
        return NoLLM()

    if p == "ollama":
        return OllamaWrapper()   # Ollama no requiere key — fallará en invoke() si no está corriendo

    # Sin proveedor definido: busca el primero disponible
    if _key_valida(settings.anthropic_api_key):
        return ClaudeWrapper()
    if _key_valida(settings.openai_api_key):
        return OpenAIWrapper()

    # Intenta Ollama como último recurso
    return OllamaWrapper()
