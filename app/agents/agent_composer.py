"""
Agente Composer — Sintetizador final de respuestas médicas

Responsabilidad única: recibir contexto de uno o más agentes especializados
(SQL y/o RAG) y generar la respuesta final profesional en español.

No consulta la BD. No busca nada. Solo compone la respuesta.
"""
from __future__ import annotations
from pathlib import Path

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "agent_composer.md"
COMPOSER_SYSTEM = _PROMPT_PATH.read_text(encoding="utf-8") if _PROMPT_PATH.exists() else ""


class ComposerAgent:
    """
    Agente sintetizador: recibe outputs de agentes especializados
    y produce la respuesta final usando el LLM.
    """

    def __init__(self, llm_provider: str | None = None) -> None:
        self._llm_provider = llm_provider
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            try:
                from app.core.llm_factory import get_llm
                self._llm = get_llm(self._llm_provider)
            except Exception:
                from app.core.llm_factory import NoLLM
                self._llm = NoLLM()
        return self._llm

    def compose(self, question: str, agent_outputs: list[dict],
                intent: str = "") -> tuple[str, str, dict | None]:
        """
        Sintetiza la respuesta final combinando los outputs de los agentes.

        Args:
            question:      Pregunta original del usuario
            agent_outputs: Lista de resultados de agentes SQL y/o RAG
            intent:        Ruta detectada por el Agente 3

        Returns:
            (respuesta_texto, nombre_proveedor, tokens_estimados)
        """
        # Construir contexto combinado de todos los agentes
        context = self._build_combined_context(agent_outputs, intent)

        llm = self._get_llm()
        provider_name = getattr(llm, "provider_name", "none")

        from app.core.llm_factory import NoLLM
        if isinstance(llm, NoLLM):
            return self._fallback(question, context), "sin-llm", None

        try:
            # Hint específico según la ruta para guiar al LLM
            hint = {
                "estadisticas":    "Resume los números de forma clara y organizada.",
                "buscar_paciente": "Presenta los datos del paciente y su historial cronológicamente.",
                "consulta_medica": "Analiza los casos clínicos encontrados y ofrece perspectiva médica.",
                "general":         "Responde de forma amigable y ofrece orientación.",
            }.get(intent, "")

            prompt = (
                f"Contexto disponible de la base de datos:\n{context}\n\n"
                f"Pregunta del usuario: {question}\n\n"
                f"{hint}\n"
                f"Genera una respuesta completa, profesional y empática."
            )

            response = llm.invoke(prompt, system=COMPOSER_SYSTEM)
            tokens = {
                "input":  len(prompt) // 4,
                "output": len(response) // 4,
                "total":  (len(prompt) + len(response)) // 4,
            }
            return response, provider_name, tokens

        except Exception as e:
            return self._fallback(question, context, error=str(e)), f"{provider_name}(error)", None

    @staticmethod
    def _build_combined_context(agent_outputs: list[dict], intent: str) -> str:
        """
        Combina los contextos de múltiples agentes en uno coherente.
        Si hay outputs de SQL y RAG, los presenta separados y complementarios.
        """
        if not agent_outputs:
            return "Sin contexto disponible."

        if len(agent_outputs) == 1:
            return agent_outputs[0].get("context", "Sin contexto.")

        # Múltiples agentes: etiquetar cada fuente
        parts = []
        for out in agent_outputs:
            agente  = out.get("agent", "desconocido").upper()
            tools   = ", ".join(out.get("tools_used", []))
            ctx     = out.get("context", "")
            if ctx:
                parts.append(f"[Fuente: Agente {agente} — tools: {tools}]\n{ctx}")

        return "\n\n---\n\n".join(parts)

    @staticmethod
    def _fallback(question: str, context: str, error: str = "") -> str:
        """Respuesta sin LLM: muestra el contexto crudo."""
        if not context.strip() or "Sin contexto" in context:
            return (
                "No se encontraron registros relacionados con tu consulta. "
                "Intenta con otras palabras clave o construye el índice RAG primero."
            )
        lines = [f"Resultados para: {question}\n", context[:2000]]
        if error:
            lines.append(
                "\n\n_(LLM no disponible: configura ANTHROPIC_API_KEY, "
                "OPENAI_API_KEY o ejecuta Ollama)_"
            )
        return "\n".join(lines)
