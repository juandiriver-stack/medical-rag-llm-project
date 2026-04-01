"""
Agente 1 — Orquestador principal  (con Langfuse observability)

Pipeline: AudioAgent → ToolAgent → LLM síntesis
Traza Langfuse por conversación con spans por cada agente/tool.
"""
import time
from sqlalchemy.orm import Session

from app.agents.audio_agent import AudioAgent
from app.agents.tool_agent import ToolAgent
from app.core.observability import observer
from app.db.models import ConversationLog
from app.tools.consultas_tool import ConsultasTool

SYNTHESIS_SYSTEM = """Eres el asistente médico del sistema San Marcos Guayaquil.
Recibes contexto con datos REALES extraídos de la base de datos.

REGLAS IMPORTANTES:
1. Si el contexto contiene datos del paciente (nombre, cédula, historial), ÚSALOS.
2. NUNCA digas que no encontraste información si el contexto SÍ tiene datos.
3. NUNCA inventes datos que no estén en el contexto.
4. Responde SIEMPRE en español, de forma clara, profesional y empática.
5. Presenta los datos del historial de forma organizada y legible."""


class MedicalOrchestratorAgent:
    def __init__(self, db: Session, llm_provider: str | None = None) -> None:
        self.db = db
        self._llm_provider = llm_provider
        self._llm = None
        tool = ConsultasTool(db)
        self.audio_agent = AudioAgent(db=db, llm_provider=llm_provider)
        self.tool_agent  = ToolAgent(tool=tool, db=db, llm_provider=llm_provider)

    def _get_llm(self):
        if self._llm is None:
            try:
                from app.core.llm_factory import get_llm
                self._llm = get_llm(self._llm_provider)
            except Exception:
                from app.core.llm_factory import NoLLM
                self._llm = NoLLM()
        return self._llm

    def handle(self, message: str, session_id: str = "default",
               audio_bytes: bytes | None = None) -> dict:

        t0 = time.time()

        # ── Langfuse: traza raíz ──────────────────────────────────────
        trace = observer.start_trace(
            name="medical_rag_pipeline",
            session_id=session_id,
            user_input=message,
            metadata={"provider": self._llm_provider or "default", "source": "audio" if audio_bytes else "text"}
        )

        # ── Agente 3 ──────────────────────────────────────────────────
        span3 = observer.span(trace, "agent3_audio",
                              input_data={"raw_input": message, "has_audio": bool(audio_bytes)})
        agent3 = self.audio_agent.process(
            raw_input=message, session_id=session_id, audio_bytes=audio_bytes
        )
        observer.end_span(span3, output={"intent": agent3["intent"], "keywords": agent3["keywords"],
                                          "clean_text": agent3["text"]})

        # ── Agente 2 ──────────────────────────────────────────────────
        span2 = observer.span(trace, "agent2_tools",
                              input_data={"intent": agent3["intent"], "text": agent3["text"]})
        agent2 = self.tool_agent.execute(agent3)
        observer.end_span(span2, output={"tools_used": agent2["tools_used"],
                                          "context_length": len(agent2["context"])})

        # ── Agente 1: síntesis ─────────────────────────────────────────
        span1 = observer.span(trace, "agent1_synthesis",
                              input_data={"context_preview": agent2["context"][:300],
                                          "question": agent3["text"]})
        response_text, provider_name, tokens = self._synthesize(agent3["text"], agent2["context"])
        observer.end_span(span1, output={"response_preview": response_text[:200],
                                          "provider": provider_name}, tokens=tokens)

        # ── Log BD ────────────────────────────────────────────────────
        self._log_safe(agent3["raw_text"], response_text, provider_name)

        elapsed = round(time.time() - t0, 3)
        result = {
            "source":       "orchestrator",
            "intent":       agent3["intent"],
            "provider":     provider_name,
            "response":     response_text,
            "tools_used":   agent2["tools_used"],
            "input_source": agent3["source"],
            "session_id":   session_id,
            "rag_used":     "rag_search" in agent2["tools_used"],
            "elapsed_s":    elapsed,
            "pipeline": {
                "agent3_intent":   agent3["intent"],
                "agent3_keywords": agent3["keywords"],
                "agent2_tools":    agent2["tools_used"],
            },
        }
        observer.end_trace(trace, output={"response_preview": response_text[:200],
                                           "elapsed_s": elapsed})
        return result

    def _synthesize(self, question: str, context: str) -> tuple[str, str, dict | None]:
        llm = self._get_llm()
        provider_name = getattr(llm, "provider_name", "none")
        from app.core.llm_factory import NoLLM
        if isinstance(llm, NoLLM):
            return self._fallback_response(question, context), "sin-llm", None
        try:
            prompt = (f"Contexto disponible:\n{context}\n\n"
                      f"Pregunta del usuario: {question}\n\n"
                      f"Proporciona una respuesta completa, profesional y empática.")
            response = llm.invoke(prompt, system=SYNTHESIS_SYSTEM)
            # Estimar tokens (1 token ≈ 4 chars)
            tokens = {
                "input":  len(prompt) // 4,
                "output": len(response) // 4,
                "total":  (len(prompt) + len(response)) // 4,
            }
            return response, provider_name, tokens
        except Exception as e:
            return self._fallback_response(question, context, error=str(e)), f"{provider_name}(error)", None

    def _log_safe(self, user_msg: str, response: str, provider: str) -> None:
        try:
            from app.db.models import ConversationLog
            self.db.add(ConversationLog(
                user_message=user_msg, agent_response=response, llm_provider=provider
            ))
            self.db.commit()
        except Exception:
            try: self.db.rollback()
            except Exception: pass

    @staticmethod
    def _fallback_response(question: str, context: str, error: str = "") -> str:
        if not context.strip() or "Sin contexto" in context:
            return ("No se encontraron registros relacionados con tu consulta. "
                    "Intenta con otras palabras clave o construye el índice RAG primero.")
        lines = [f"Resultados para: {question}\n", context[:2000]]
        if error:
            lines.append("\n\n_(LLM no disponible: configura ANTHROPIC_API_KEY, OPENAI_API_KEY o ejecuta Ollama)_")
        return "\n".join(lines)
