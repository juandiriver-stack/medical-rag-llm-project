"""
Agente RAG — Especialista en búsqueda semántica clínica

Responsabilidad única: encontrar registros médicos relevantes
usando búsqueda híbrida TF-IDF + BM25 + Reciprocal Rank Fusion.

No busca pacientes por nombre/ID (eso es del Agente SQL).
No calcula estadísticas (eso es del Agente SQL).
Retorna fragmentos relevantes con score de similitud.
"""
from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

from app.rag.engine import rag_engine


def _anonimizar_nombre(nombre: str) -> str:
    """
    Enmascara el nombre real del paciente para el contexto que va al LLM.
    Conserva la primera y última letra de cada palabra.
    Ej: 'Erika Maribel Chasin Caicedo' → 'Exxxxxe Mxxxxxxl Cxxxxn Cxxxxdo'
    El médico sigue viendo el nombre real en pantalla (metadata.nombre_paciente).
    """
    if not nombre:
        return ""
    palabras = []
    for palabra in nombre.strip().split():
        if len(palabra) <= 2:
            palabras.append(palabra)
        elif len(palabra) == 3:
            palabras.append(palabra[0] + "x" + palabra[-1])
        else:
            palabras.append(palabra[0] + "x" * (len(palabra) - 2) + palabra[-1])
    return " ".join(palabras)
from app.core.ndcg_metrics import RAGMetrics

_rag_metrics = RAGMetrics()

if TYPE_CHECKING:
    from app.tools.consultas_tool import ConsultasTool

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "agent_rag.md"
RAG_AGENT_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8") if _PROMPT_PATH.exists() else ""


class RAGAgent:
    """
    Agente especializado en búsqueda semántica sobre registros clínicos.
    Combina RAG semántico con búsqueda SQL por keyword para máxima precisión.
    """

    def __init__(self, tool: "ConsultasTool") -> None:
        self.tool = tool

    def execute(self, text: str, keywords: list) -> dict:
        """
        Búsqueda híbrida:
          1. RAG semántico (TF-IDF + BM25 + RRF) sobre índice en memoria
          2. SQL keyword sobre motivoConsulta, enfermedadActual, examenFisico
        Combina ambos resultados en un contexto enriquecido.
        """
        parts     = []
        raw_data  = {}
        tools_used = []

        # ── Búsqueda semántica RAG ─────────────────────────────────────
        if rag_engine.is_built:
            resultados = rag_engine.search(text)
            raw_data["rag_results"] = resultados
            tools_used.append("rag_search")

            if resultados:
                fragmentos = []
                for i, r in enumerate(resultados, 1):
                    meta = r.get("metadata", {})
                    pac  = meta.get("nombre_paciente", "")
                    hdr  = f"[{i}] relevancia={r['score']:.4f}"
                    if pac:
                        # Anonimizar nombre antes de enviar al LLM
                        hdr += f" | paciente: {_anonimizar_nombre(pac)}"
                    fragmentos.append(f"{hdr}\n{r['text']}")
                parts.append("Registros clínicos relevantes (búsqueda semántica):\n" + "\n\n".join(fragmentos))
            else:
                parts.append("La búsqueda semántica no encontró registros relevantes.")
        else:
            parts.append("(Índice RAG no construido — usa el botón 'Construir índice')")

        # ── Complemento SQL por keyword ────────────────────────────────
        kw = " ".join(keywords[:2]) if keywords else text[:60]
        if kw.strip():
            try:
                sql_data = self.tool.search_consultas_by_motivo(kw, limit=5)
                raw_data["sql_keyword"] = sql_data
                tools_used.append("sql_consultas")

                items = sql_data.get("items", [])
                if items:
                    filas = []
                    for c in items:
                        nombre_raw = c.get("nombre_paciente") or ""
                        # Anonimizar nombre antes de enviar al LLM
                        nombre = _anonimizar_nombre(nombre_raw) if nombre_raw else f"ID {c.get('id_paciente','?')}"
                        motivo = (c.get("motivo_consulta")   or "-")[:100]
                        enf    = (c.get("enfermedad_actual") or "-")[:80]
                        filas.append(f"  • [{nombre}] {motivo} | {enf}")
                    parts.append(
                        f"Consultas con keyword '{kw}' ({sql_data['count']} resultados):\n"
                        + "\n".join(filas)
                    )
            except Exception as e:
                raw_data["sql_keyword_error"] = str(e)

        ctx = "\n\n".join(parts) if parts else "Sin resultados clínicos relevantes."

        # Recuperar chunks RAG para métricas (pueden ser de raw_data)
        rag_chunks = raw_data.get("rag_results", [])

        # Calcular métricas RAG automáticamente desde los RRF scores
        rrf_scores  = [r.get("score", 0) for r in rag_chunks] if rag_chunks else []
        relevances  = _rag_metrics.relevances_from_rrf(rrf_scores, umbral=0.01)
        rag_metrics = _rag_metrics.compute_all(relevances, k=min(5, len(relevances)))

        return {
            "agent":       "rag",
            "tools_used":  tools_used,
            "raw_data":    raw_data,
            "context":     ctx,
            "intent":      "consulta_medica",
            "rag_metrics": rag_metrics,   # Precision, Recall, NDCG, MRR
        }
