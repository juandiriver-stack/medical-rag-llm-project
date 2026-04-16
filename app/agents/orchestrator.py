"""
Agente 1 — Orquestador principal

Pipeline con 5 agentes + métricas de usabilidad completas en Langfuse:

  Scores por span (latency_ms, rows_returned, chunks_retrieved...)
  Scores globales de la traza via UsabilityMetrics:
    - Latencia por agente: total, llm, sql, rag, audio
    - Usabilidad RAG: chunks_retrieved, chunks_used_est, chunks_ratio, rrf_scores
    - Distribución de errores: error, error_llm, error_sql, error_rag
    - Volumen: sql_queries_executed, rag_used, intent_code, n_agents_invoked
"""
import time
from sqlalchemy.orm import Session

from app.agents.audio_agent        import AudioAgent
from app.agents.agent_data_science import DataScienceAgent
from app.agents.agent_patient      import PatientAgent
from app.agents.agent_rag          import RAGAgent
from app.agents.agent_composer     import ComposerAgent
from app.core.observability        import observer
from app.core.metrics              import UsabilityMetrics
from app.tools.consultas_tool      import ConsultasTool

RUTAS: dict[str, list[str]] = {
    "estadisticas":    ["data_science"],
    "buscar_paciente": ["patient"],
    "consulta_medica": ["rag", "patient"],
    "general":         ["rag"],
}

TOOLS_POR_RUTA: dict[str, list[str]] = {
    "estadisticas":    ["sql_summary"],
    "buscar_paciente": ["sql_pacientes", "sql_historial"],
    "consulta_medica": ["rag_search", "sql_consultas"],
    "general":         ["rag_search"],
}


def _ms(t0: float) -> float:
    return round((time.time() - t0) * 1000, 2)


class MedicalOrchestratorAgent:
    def __init__(self, db: Session, llm_provider: str | None = None) -> None:
        self.db = db
        self._llm_provider = llm_provider
        tool = ConsultasTool(db)
        self.audio_agent        = AudioAgent(db=db, llm_provider=llm_provider)
        self.data_science_agent = DataScienceAgent(tool=tool)
        self.patient_agent      = PatientAgent(tool=tool, db=db)
        self.rag_agent          = RAGAgent(tool=tool)
        self.composer_agent     = ComposerAgent(llm_provider=llm_provider)

    def handle(self, message: str, session_id: str = "default",
               audio_bytes: bytes | None = None) -> dict:

        t_total = time.time()
        m = UsabilityMetrics()          # acumulador de métricas

        trace = observer.start_trace(
            name="medical_rag_pipeline",
            session_id=session_id,
            user_input=message,
            metadata={"provider": self._llm_provider or "default",
                      "source":   "audio" if audio_bytes else "text"}
        )

        # ── Agente 3: AudioAgent ──────────────────────────────────────
        t0 = time.time()
        span3 = observer.span(trace, "agent3_audio", as_type="agent",
                              input_data={"raw_input": message})
        agent3   = self.audio_agent.process(
            raw_input=message, session_id=session_id, audio_bytes=audio_bytes
        )
        intent   = agent3["intent"]
        keywords = agent3["keywords"]
        text     = agent3["text"]
        m.set_lat_audio(_ms(t0))
        m.intent = intent

        observer.end_span(span3,
            output={"intent": intent, "keywords": keywords,
                    "ruta_fija": RUTAS.get(intent, ["rag"])},
            latency_ms=m.lat_audio
        )

        agentes_a_invocar = RUTAS.get(intent, ["rag"])
        agent_outputs     = []
        all_tools_used    = []

        # ── DataScienceAgent ──────────────────────────────────────────
        if "data_science" in agentes_a_invocar:
            t0 = time.time()
            span_ds = observer.span(trace, "agent_data_science", as_type="tool",
                                    input_data={"intent": intent, "text": text})
            try:
                ds_result = self.data_science_agent.execute(text)
                agent_outputs.append(ds_result)
                all_tools_used.extend(ds_result.get("tools_used", []))
                m.set_lat_ds(_ms(t0))

                sql_ejecutado = ("SELECT COUNT(*) total_consultas FROM consultas; "
                                 "SELECT COUNT(*) total_pacientes FROM paciente;")
                observer.end_span(span_ds,
                    output={
                        "tools_used":      ds_result["tools_used"],
                        "sql_ejecutado":   sql_ejecutado,
                        "metricas":        ds_result.get("metricas", {}),
                        "context_preview": ds_result["context"][:300],
                    },
                    latency_ms=m.lat_ds,
                    scores={"rows_returned": 1.0}
                )
            except Exception as e:
                m.mark_error_sql()
                observer.end_span(span_ds,
                    output={"error": str(e), "tipo_error": "indisponibilidad_sql"},
                    latency_ms=_ms(t0)
                )

        # ── PatientAgent ──────────────────────────────────────────────
        if "patient" in agentes_a_invocar:
            t0 = time.time()
            span_pat = observer.span(trace, "agent_patient", as_type="tool",
                                     input_data={"intent": intent, "text": text,
                                                  "keywords": keywords})
            try:
                pat_result = self.patient_agent.execute(text, keywords)
                agent_outputs.append(pat_result)
                all_tools_used.extend(pat_result.get("tools_used", []))
                m.set_lat_patient(_ms(t0))

                from app.agents.agent_patient import _extraer_id, _extraer_cedula
                pid = _extraer_id(text)
                ced = _extraer_cedula(text)
                entidad = (f"ID={pid}" if pid else
                           f"cedula={ced}" if ced else "nombre/lista")

                if pid:
                    sql_desc = (f"SELECT * FROM paciente WHERE ID_PACIENTE = {pid}; "
                                f"SELECT * FROM consultas WHERE idpacientes = {pid} "
                                f"ORDER BY fecha DESC LIMIT 100;")
                elif ced:
                    sql_desc = (f"SELECT ID_PACIENTE FROM paciente "
                                f"WHERE IDENTIFICACION = '{ced}';")
                else:
                    sql_desc = "SELECT * FROM paciente ORDER BY ID_PACIENTE LIMIT 15;"

                historial  = pat_result.get("raw_data", {}).get("historial", {})
                n_consultas = (len(historial.get("items", []))
                               if isinstance(historial, dict) else 0)

                observer.end_span(span_pat,
                    output={
                        "tools_used":           pat_result["tools_used"],
                        "entidad_buscada":      entidad,
                        "sql_ejecutado":        sql_desc,
                        "consultas_encontradas": n_consultas,
                        "context_length":       len(pat_result["context"]),
                    },
                    latency_ms=m.lat_patient,
                    scores={"rows_returned": float(n_consultas)}
                )
            except Exception as e:
                m.mark_error_sql()
                observer.end_span(span_pat,
                    output={"error": str(e), "tipo_error": "indisponibilidad_sql"},
                    latency_ms=_ms(t0)
                )

        # ── RAGAgent ──────────────────────────────────────────────────
        if "rag" in agentes_a_invocar:
            t0 = time.time()
            span_rag = observer.span(trace, "agent_rag", as_type="retriever",
                                     input_data={"query": text, "keywords": keywords})
            try:
                rag_result = self.rag_agent.execute(text, keywords)
                agent_outputs.append(rag_result)
                all_tools_used.extend(rag_result.get("tools_used", []))
                m.set_lat_rag(_ms(t0))

                rag_chunks = rag_result.get("raw_data", {}).get("rag_results", [])
                m.set_rag_chunks(rag_chunks)         # registra chunks en métricas

                chunks_detalle = [
                    {
                        "rank":          i + 1,
                        "rrf_score":     round(c.get("score", 0), 6),
                        "id_consulta":   c.get("metadata", {}).get("id_consulta"),
                        "id_paciente":   c.get("metadata", {}).get("id_paciente"),
                        "nombre_pac":    c.get("metadata", {}).get("nombre_paciente"),
                        "fecha":         c.get("metadata", {}).get("fecha"),
                        "texto_preview": c.get("text", "")[:120],
                    }
                    for i, c in enumerate(rag_chunks)
                ]

                kw = " ".join(keywords[:2]) if keywords else text[:40]
                sql_keyword = (
                    f"SELECT c.*, p.NOMBRES, p.APELLIDOS FROM consultas c "
                    f"LEFT JOIN paciente p ON p.ID_PACIENTE = c.idpacientes "
                    f"WHERE c.motivoConsulta LIKE '%{kw}%' "
                    f"OR c.enfermedadActual LIKE '%{kw}%' "
                    f"OR c.examenFisico LIKE '%{kw}%' LIMIT 5;"
                )

                observer.end_span(span_rag,
                    output={
                        "tools_used":     rag_result["tools_used"],
                        "query":          text,
                        "n_chunks_rag":   m.chunks_retrieved,
                        "chunks_used_est":m._chunks_used_estimate(),
                        "chunks_ratio":   m._chunks_ratio(),
                        "chunks_detalle": chunks_detalle,
                        "avg_rrf_score":  m._avg_rrf(),
                        "top_rrf_score":  m._top_rrf(),
                        "sql_keyword":    sql_keyword,
                        "context_length": len(rag_result["context"]),
                    },
                    latency_ms=m.lat_rag,
                    scores={
                        "chunks_retrieved": float(m.chunks_retrieved),
                        "chunks_used_est":  float(m._chunks_used_estimate()),
                        "chunks_ratio":     m._chunks_ratio(),
                        "top_rrf_score":    m._top_rrf(),
                        "avg_rrf_score":    m._avg_rrf(),
                    }
                )
            except Exception as e:
                m.mark_error_rag()
                observer.end_span(span_rag,
                    output={"error": str(e), "tipo_error": "indisponibilidad_rag"},
                    latency_ms=_ms(t0)
                )

        # ── ComposerAgent ─────────────────────────────────────────────
        t0 = time.time()
        span_comp = observer.span(trace, "agent_composer", as_type="agent",
                                  input_data={
                                      "question":       text,
                                      "intent":         intent,
                                      "n_fuentes":      len(agent_outputs),
                                      "agentes_usados": agentes_a_invocar,
                                  })
        try:
            response_text, provider_name, tokens = self.composer_agent.compose(
                question=text, agent_outputs=agent_outputs, intent=intent
            )
            m.set_lat_llm(_ms(t0))
            observer.end_span(span_comp,
                output={
                    "response_preview": response_text[:300],
                    "provider":         provider_name,
                    "tokens":           tokens or {},
                },
                tokens=tokens,
                latency_ms=m.lat_llm
            )
        except Exception as e:
            m.mark_error_llm()
            response_text  = self._fallback(text, agent_outputs)
            provider_name  = "error"
            observer.end_span(span_comp,
                output={"error": str(e), "tipo_error": "indisponibilidad_llm"},
                latency_ms=_ms(t0)
            )

        # ── Finalizar métricas ─────────────────────────────────────────
        m.set_lat_total(_ms(t_total))
        m.tools_used      = list(dict.fromkeys(all_tools_used))
        m.n_agent_outputs = len(agent_outputs)

        # Log consola
        print(f"INFO  Métricas conversación [{session_id}]:\n{m.summary()}")

        self._log_safe(agent3["raw_text"], response_text, provider_name)

        # ── Cerrar traza con TODAS las métricas de usabilidad ─────────
        observer.end_trace(trace,
            output={
                "intent":           intent,
                "agentes_usados":   agentes_a_invocar,
                "elapsed_ms":       m.lat_total,
                "response_preview": response_text[:200],
            },
            scores=m.to_scores()      # ← todas las métricas de una vez
        )

        return {
            "source":       "orchestrator",
            "intent":       intent,
            "ruta":         RUTAS.get(intent, ["rag"]),
            "provider":     provider_name,
            "response":     response_text,
            "tools_used":   m.tools_used,
            "input_source": agent3["source"],
            "session_id":   session_id,
            "rag_used":     "rag_search" in all_tools_used,
            "elapsed_ms":   m.lat_total,
            "metricas":     m.to_scores(),
            "pipeline": {
                "agent3_intent":   intent,
                "agent3_keywords": keywords,
                "agentes_usados":  agentes_a_invocar,
                "tools_used":      m.tools_used,
                "ruta_fija":       RUTAS.get(intent),
            },
        }

    def _fallback(self, question: str, outputs: list) -> str:
        ctx = "\n".join(o.get("context", "") for o in outputs)
        return f"Resultados para: {question}\n{ctx[:1500]}"

    def _log_safe(self, user_msg: str, response: str, provider: str) -> None:
        try:
            from app.db.models import ConversationLog
            self.db.add(ConversationLog(
                user_message=user_msg,
                agent_response=response,
                llm_provider=provider,
            ))
            self.db.commit()
        except Exception:
            try: self.db.rollback()
            except Exception: pass
