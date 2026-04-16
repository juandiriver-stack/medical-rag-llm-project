"""
Agente Data Science — Especialista en estadísticas clínicas

Responsabilidad única: calcular métricas y estadísticas globales.
No busca pacientes. No hace RAG.
"""
from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.tools.consultas_tool import ConsultasTool

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "agent_data_science.md"
DS_AGENT_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8") if _PROMPT_PATH.exists() else ""


class DataScienceAgent:
    """
    Agente especializado exclusivamente en estadísticas globales.
    Ejecuta sql_summary y retorna métricas estructuradas en JSON.
    """

    def __init__(self, tool: "ConsultasTool") -> None:
        self.tool = tool

    def execute(self, text: str = "") -> dict:
        """
        Obtiene estadísticas globales del sistema.
        No requiere entidad específica — siempre retorna el resumen completo.
        """
        try:
            data    = self.tool.summary()
            total_c = data["total_consultas"]
            total_p = data["total_pacientes"]
            con_mot = data["con_motivo_registrado"]
            sin_mot = total_c - con_mot
            pct_doc = round((con_mot / total_c * 100), 1) if total_c > 0 else 0

            ctx = (
                f"Estadísticas del sistema San Marcos Guayaquil:\n"
                f"  Total consultas registradas:      {total_c:,}\n"
                f"  Total pacientes registrados:       {total_p:,}\n"
                f"  Consultas con motivo documentado:  {con_mot:,} ({pct_doc}%)\n"
                f"  Consultas sin motivo documentado:  {sin_mot:,}\n"
                f"  Tabla paciente activa:             "
                f"{'Sí' if data.get('tabla_paciente_activa') else 'No'}"
            )

            return {
                "agent":      "data_science",
                "tools_used": ["sql_summary"],
                "raw_data":   data,
                "context":    ctx,
                "intent":     "estadisticas",
                "metricas": {
                    "total_consultas":        total_c,
                    "total_pacientes":        total_p,
                    "con_motivo_registrado":  con_mot,
                    "sin_motivo_registrado":  sin_mot,
                    "porcentaje_documentado": pct_doc,
                }
            }
        except Exception as e:
            return {
                "agent":      "data_science",
                "tools_used": ["sql_summary"],
                "raw_data":   {},
                "context":    f"Error al obtener estadísticas: {e}",
                "intent":     "estadisticas",
            }
