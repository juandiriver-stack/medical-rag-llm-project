"""
Módulo de métricas de usabilidad para Langfuse

Calcula y registra en el dashboard de Langfuse las métricas
solicitadas por el tutor:

  MÉTRICAS DE LATENCIA
    total_latency_ms      → tiempo total de la conversación
    llm_latency_ms        → solo el LLM (ComposerAgent)
    sql_latency_ms        → solo el agente SQL
    rag_latency_ms        → solo el agente RAG

  MÉTRICAS DE USABILIDAD RAG
    chunks_retrieved      → chunks obtenidos por búsqueda RAG
    chunks_used_est       → estimación de chunks usados en respuesta
    chunks_ratio          → chunks_used / chunks_retrieved (0.0-1.0)
    top_rrf_score         → score de similitud del chunk más relevante
    avg_rrf_score         → score promedio de todos los chunks

  MÉTRICAS DE ERROR (distribución por tipo)
    error                 → 1.0 si hubo cualquier error, 0.0 si exitoso
    error_llm             → 1.0 si falló el LLM (indisponibilidad servicio)
    error_sql             → 1.0 si falló alguna consulta SQL
    error_rag             → 1.0 si falló el índice RAG

  MÉTRICAS DE VOLUMEN
    sql_queries_executed  → número de consultas SQL ejecutadas
    rag_used              → 1.0 si se usó RAG, 0.0 si no
    intent_code           → int codificado por intent (para histogramas)
"""
from __future__ import annotations
from typing import Any


# ── Codificación de intents para histogramas numéricos ─────────────────────
INTENT_CODES = {
    "estadisticas":    1.0,
    "buscar_paciente": 2.0,
    "consulta_medica": 3.0,
    "general":         4.0,
}

# ── Mapeo de herramientas → número de queries SQL ──────────────────────────
SQL_QUERIES_POR_TOOL = {
    "sql_summary":    1,
    "sql_pacientes":  1,
    "sql_historial":  1,
    "sql_consultas":  1,
}


class UsabilityMetrics:
    """
    Acumula métricas durante el pipeline y las registra en Langfuse
    al finalizar la conversación.
    """

    def __init__(self) -> None:
        # Latencias por agente (ms)
        self.lat_audio:   float = 0.0
        self.lat_ds:      float = 0.0
        self.lat_patient: float = 0.0
        self.lat_rag:     float = 0.0
        self.lat_llm:     float = 0.0
        self.lat_total:   float = 0.0

        # RAG
        self.chunks_retrieved: int   = 0
        self.rrf_scores:       list  = []

        # Errores por tipo
        self.error_llm:  bool = False
        self.error_sql:  bool = False
        self.error_rag:  bool = False

        # Volumen
        self.tools_used:       list[str] = []
        self.intent:           str = "general"
        self.n_agent_outputs:  int = 0

    # ── Registrar latencias ────────────────────────────────────────────
    def set_lat_audio(self, ms: float):   self.lat_audio   = ms
    def set_lat_ds(self, ms: float):      self.lat_ds      = ms
    def set_lat_patient(self, ms: float): self.lat_patient = ms
    def set_lat_rag(self, ms: float):     self.lat_rag     = ms
    def set_lat_llm(self, ms: float):     self.lat_llm     = ms
    def set_lat_total(self, ms: float):   self.lat_total   = ms

    # ── Registrar RAG ──────────────────────────────────────────────────
    def set_rag_chunks(self, chunks: list) -> None:
        self.chunks_retrieved = len(chunks)
        self.rrf_scores = [c.get("score", 0.0) for c in chunks if "score" in c]

    # ── Registrar errores ──────────────────────────────────────────────
    def mark_error_llm(self): self.error_llm = True
    def mark_error_sql(self): self.error_sql = True
    def mark_error_rag(self): self.error_rag = True

    # ── Calcular métricas derivadas ────────────────────────────────────
    def _sql_queries_count(self) -> int:
        """Cuenta el número de queries SQL ejecutadas según las tools usadas."""
        return sum(SQL_QUERIES_POR_TOOL.get(t, 0) for t in self.tools_used)

    def _chunks_used_estimate(self) -> int:
        """
        Estimación de chunks efectivamente usados en la respuesta.
        El ComposerAgent incluye todos los chunks en el contexto, pero
        en la práctica el LLM usa los más relevantes (top_k filtrado por umbral 0.01).
        """
        if not self.rrf_scores:
            return 0
        UMBRAL = 0.01
        return sum(1 for s in self.rrf_scores if s >= UMBRAL)

    def _chunks_ratio(self) -> float:
        """Ratio chunks_used / chunks_retrieved (0.0 - 1.0)."""
        if self.chunks_retrieved == 0:
            return 0.0
        used = self._chunks_used_estimate()
        return round(used / self.chunks_retrieved, 3)

    def _top_rrf(self) -> float:
        return round(max(self.rrf_scores), 6) if self.rrf_scores else 0.0

    def _avg_rrf(self) -> float:
        if not self.rrf_scores:
            return 0.0
        return round(sum(self.rrf_scores) / len(self.rrf_scores), 6)

    # ── Construir dict completo para Langfuse ─────────────────────────
    def to_scores(self) -> dict[str, float]:
        """
        Retorna todas las métricas como dict float para registrar
        en Langfuse con score_trace().
        """
        any_error = self.error_llm or self.error_sql or self.error_rag
        sql_lat   = max(self.lat_ds, self.lat_patient)  # el más lento

        scores: dict[str, float] = {
            # Latencia
            "total_latency_ms":   self.lat_total,
            "llm_latency_ms":     self.lat_llm,
            "sql_latency_ms":     sql_lat,
            "rag_latency_ms":     self.lat_rag,
            "audio_latency_ms":   self.lat_audio,

            # Usabilidad RAG
            "chunks_retrieved":   float(self.chunks_retrieved),
            "chunks_used_est":    float(self._chunks_used_estimate()),
            "chunks_ratio":       self._chunks_ratio(),
            "top_rrf_score":      self._top_rrf(),
            "avg_rrf_score":      self._avg_rrf(),

            # Errores (distribución)
            "error":              1.0 if any_error   else 0.0,
            "error_llm":          1.0 if self.error_llm else 0.0,
            "error_sql":          1.0 if self.error_sql else 0.0,
            "error_rag":          1.0 if self.error_rag else 0.0,

            # Volumen
            "sql_queries_executed": float(self._sql_queries_count()),
            "rag_used":             1.0 if "rag_search" in self.tools_used else 0.0,
            "intent_code":          INTENT_CODES.get(self.intent, 0.0),
            "n_agents_invoked":     float(self.n_agent_outputs),
        }
        return {k: v for k, v in scores.items() if v is not None}

    def summary(self) -> str:
        """Resumen legible para logs de consola."""
        scores = self.to_scores()
        lines = [
            f"  total_latency_ms:     {scores['total_latency_ms']:.0f}",
            f"  llm_latency_ms:       {scores['llm_latency_ms']:.0f}",
            f"  sql_latency_ms:       {scores['sql_latency_ms']:.0f}",
            f"  rag_latency_ms:       {scores['rag_latency_ms']:.0f}",
            f"  chunks_retrieved:     {int(scores['chunks_retrieved'])}",
            f"  chunks_used_est:      {int(scores['chunks_used_est'])}",
            f"  chunks_ratio:         {scores['chunks_ratio']:.2f}",
            f"  top_rrf_score:        {scores['top_rrf_score']:.4f}",
            f"  sql_queries_executed: {int(scores['sql_queries_executed'])}",
            f"  error: {scores['error']} "
            f"(llm={scores['error_llm']}, sql={scores['error_sql']}, rag={scores['error_rag']})",
        ]
        return "\n".join(lines)
