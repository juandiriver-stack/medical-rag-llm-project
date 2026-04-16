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
                        hdr += f" | paciente: {pac}"
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
                        nombre = c.get("nombre_paciente") or f"ID {c.get('id_paciente','?')}"
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

        return {
            "agent":      "rag",
            "tools_used": tools_used,
            "raw_data":   raw_data,
            "context":    ctx,
            "intent":     "consulta_medica",
        }
