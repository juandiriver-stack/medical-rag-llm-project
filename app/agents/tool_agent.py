"""
Agente 2 — Tool Agent  (módulo independiente)
Prompt cargado desde: app/prompts/agent_tools.md
Detecta: ID numérico | cédula | nombre MAYÚSCULAS | keywords médicos
"""
from __future__ import annotations
import json, re
from pathlib import Path
from typing import TYPE_CHECKING

from app.rag.engine import rag_engine
from app.core.observability import observer

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.tools.consultas_tool import ConsultasTool

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "agent_tools.md"
TOOL_SYSTEM = _PROMPT_PATH.read_text(encoding="utf-8") if _PROMPT_PATH.exists() else ""

_TOOL_RULES = {
    "estadisticas":    ["sql_summary"],
    "consulta_medica": ["rag_search", "sql_consultas"],
    "general":         ["rag_search"],
}

# ── Detectores ────────────────────────────────────────────────────────
def _extraer_id_paciente(text: str) -> int | None:
    patrones = [
        r"(?:paciente\s+(?:id|#|n[uú]mero)?\s*[:=]?\s*)(\d{3,7})",
        r"(?:id\s*[:=]\s*)(\d{3,7})",
        r"(?:historial\s+(?:del\s+)?(?:paciente\s+)?(?:id\s+)?)(\d{3,7})",
        r"(?:#\s*)(\d{3,7})",
        r"\b(\d{5,7})\b",
    ]
    for p in patrones:
        m = re.search(p, text.lower())
        if m: return int(m.group(1))
    return None

def _extraer_cedula(text: str) -> str | None:
    patrones = [
        r"(?:c[eé]dula|identificaci[oó]n|ci|ruc)\s*[:=]?\s*(\d{6,13})",
        r"\b(\d{10})\b",
    ]
    for p in patrones:
        m = re.search(p, text.lower())
        if m: return m.group(1)
    return None

def _es_busqueda_por_nombre(text: str) -> bool:
    triggers = [
        r"consultas\s+(?:de[l]?\s+)?(?:paciente\s+)?[A-ZÁÉÍÓÚÑ]{2,}",
        r"historial\s+(?:cl[ií]nico\s+)?(?:de[l]?\s+)?(?:paciente\s+)?[A-ZÁÉÍÓÚÑ]{2,}",
        r"resumen\s+(?:de\s+)?(?:consultas?\s+)?(?:de[l]?\s+)?(?:paciente\s+)?[A-ZÁÉÍÓÚÑ]{2,}",
        r"paciente\s+[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ]+\s+[A-ZÁÉÍÓÚÑ]",
        r"buscar?\s+(?:a\s+)?(?:paciente\s+)?[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ]+",
        r"datos\s+(?:de[l]?\s+)?(?:paciente\s+)?[A-ZÁÉÍÓÚÑ]{2,}",
        r"información\s+(?:de[l]?\s+)?(?:paciente\s+)?[A-ZÁÉÍÓÚÑ]{2,}",
        r"expediente\s+(?:de[l]?\s+)?(?:paciente\s+)?[A-ZÁÉÍÓÚÑ]{2,}",
        r"ver\s+(?:las?\s+)?(?:consultas?\s+)?(?:de[l]?\s+)?(?:paciente\s+)?[A-ZÁÉÍÓÚÑ]{2,}",
    ]
    for p in triggers:
        if re.search(p, text): return True
    palabras = text.strip().split()
    if len(palabras) >= 2 and sum(1 for p in palabras if p.isupper() and len(p) > 1) >= 2:
        return True
    return False

def _extraer_nombre(text: str) -> str | None:
    prefijos = [
        r"^resumen\s+(?:de\s+)?(?:consultas?\s+)?(?:de[l]?\s+)?(?:paciente\s+)?",
        r"^consultas?\s+(?:de[l]?\s+)?(?:paciente\s+)?",
        r"^historial\s+(?:cl[ií]nico\s+)?(?:de[l]?\s+)?(?:paciente\s+)?",
        r"^expediente\s+(?:de[l]?\s+)?(?:paciente\s+)?",
        r"^datos?\s+(?:de[l]?\s+)?(?:paciente\s+)?",
        r"^información\s+(?:de[l]?\s+)?(?:paciente\s+)?",
        r"^ver\s+(?:las?\s+)?(?:consultas?\s+)?(?:de[l]?\s+)?(?:paciente\s+)?",
        r"^paciente\s+",
        r"^buscar?\s+(?:a\s+)?(?:paciente\s+)?",
        r"^dame\s+(?:las?\s+)?(?:consultas?\s+)?(?:de[l]?\s+)?(?:paciente\s+)?",
    ]
    nombre = text.strip()
    for _ in range(3):
        antes = nombre
        for p in prefijos:
            nombre = re.sub(p, "", nombre, flags=re.IGNORECASE).strip()
        if nombre == antes: break
    nombre = re.sub(r"\s+(por favor|gracias|porfavor|ok).*$", "", nombre, flags=re.IGNORECASE)
    nombre = nombre.strip(".,?¿!¡ \t")
    return nombre if len(nombre) >= 3 else None


class ToolAgent:
    """Agente 2: selecciona y ejecuta herramientas, retorna JSON."""

    def __init__(self, tool: "ConsultasTool", db: "Session",
                 llm_provider: str | None = None) -> None:
        self.tool = tool
        self.db   = db
        self._llm_provider = llm_provider
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            try:
                from app.core.llm_factory import get_llm
                self._llm = get_llm(self._llm_provider)
            except Exception: pass
        return self._llm

    def execute(self, agent3_result: dict, trace=None) -> dict:
        intent   = agent3_result.get("intent", "general")
        text     = agent3_result.get("text", "")
        keywords = agent3_result.get("keywords", [])

        pid = _extraer_id_paciente(text)
        if pid: return self._por_id(pid, text)

        ced = _extraer_cedula(text)
        if ced: return self._por_cedula(ced, text)

        if _es_busqueda_por_nombre(text):
            nombre = _extraer_nombre(text)
            if nombre: return self._por_nombre(nombre, text)

        return self._por_intent(intent, text, keywords)

    # ── Búsquedas directas ────────────────────────────────────────────
    def _por_id(self, pid: int, text: str) -> dict:
        paciente   = self.tool.get_paciente(pid)
        consultas  = self.tool.consultas_por_paciente(pid)
        return {"context": self._fmt_paciente_ctx(paciente, consultas),
                "tools_used": ["sql_pacientes", "sql_historial"],
                "raw_data": {"paciente": paciente, "historial": consultas},
                "intent": "buscar_paciente", "original_text": text}

    def _por_cedula(self, cedula: str, text: str) -> dict:
        from sqlalchemy import text as sqlt
        try:
            row = self.db.execute(
                sqlt("SELECT ID_PACIENTE FROM paciente WHERE IDENTIFICACION = :c LIMIT 1"),
                {"c": cedula}).fetchone()
            if row: return self._por_id(row.ID_PACIENTE, text)
        except Exception: pass
        return {"context": f"No se encontró paciente con cédula {cedula}.",
                "tools_used": ["sql_pacientes"], "raw_data": {}, "intent": "buscar_paciente", "original_text": text}

    def _por_nombre(self, nombre: str, text: str) -> dict:
        from sqlalchemy import text as sqlt
        try:
            palabras = nombre.upper().split()
            if len(palabras) >= 2:
                cond   = " AND ".join(f"CONCAT(NOMBRES,' ',APELLIDOS) LIKE :p{i}" for i in range(len(palabras)))
                params = {f"p{i}": f"%{p}%" for i, p in enumerate(palabras)}
            else:
                cond   = "CONCAT(NOMBRES,' ',APELLIDOS) LIKE :p0"
                params = {"p0": f"%{palabras[0]}%"}
            rows = self.db.execute(sqlt(f"SELECT ID_PACIENTE FROM paciente WHERE {cond} LIMIT 5"), params).fetchall()
            if not rows:
                return {"context": f"No se encontró paciente con nombre '{nombre}'.",
                        "tools_used": ["sql_pacientes"], "raw_data": {}, "intent": "buscar_paciente", "original_text": text}
            if len(rows) == 1:
                return self._por_id(rows[0].ID_PACIENTE, text)
            pacientes = [self.tool.get_paciente(r.ID_PACIENTE) for r in rows]
            lista = "\n".join(
                f"  • ID {p['data']['id_paciente']} — {p['data'].get('nombre_completo','?')} | Cédula: {p['data'].get('cedula','-')}"
                for p in pacientes if p.get("type") == "paciente_detail"
            )
            return {"context": f"Se encontraron {len(rows)} pacientes con '{nombre}':\n{lista}\n\nEscribe el ID para ver su historial.",
                    "tools_used": ["sql_pacientes"], "raw_data": {"pacientes": pacientes},
                    "intent": "buscar_paciente", "original_text": text}
        except Exception as e:
            return {"context": f"Error al buscar por nombre: {e}",
                    "tools_used": ["sql_pacientes"], "raw_data": {}, "intent": "buscar_paciente", "original_text": text}

    def _por_intent(self, intent: str, text: str, keywords: list) -> dict:
        tools_to_run = _TOOL_RULES.get(intent, ["rag_search"])
        parts, raw = [], {}
        for tool_name in tools_to_run:
            try:
                if tool_name == "sql_consultas":
                    kw   = " ".join(keywords[:2]) or text[:50]
                    data = self.tool.search_consultas_by_motivo(kw, limit=5)
                    raw["sql_consultas"] = data
                    if data.get("items"):
                        filas = "\n".join(self._fmt_consulta(c) for c in data["items"])
                        parts.append(f"Consultas encontradas ({data['count']}):\n{filas}")
                elif tool_name == "sql_summary":
                    data = self.tool.summary()
                    raw["sql_summary"] = data
                    parts.append(f"Estadísticas: {data['total_consultas']} consultas, {data['total_pacientes']} pacientes.")
                elif tool_name == "rag_search":
                    if rag_engine.is_built:
                        ctx = rag_engine.build_context(text)
                        raw["rag_search"] = ctx
                        parts.append(f"Contexto RAG híbrido:\n{ctx}")
                    else:
                        parts.append("(Índice RAG no construido — usa 'Construir índice')")
            except Exception as e:
                raw[tool_name] = f"error: {e}"
        ctx = "\n\n".join(parts) if parts else "Sin contexto adicional."
        return {"context": ctx, "tools_used": tools_to_run, "raw_data": raw,
                "intent": intent, "original_text": text}

    # ── Formatters ────────────────────────────────────────────────────
    @staticmethod
    def _fmt_paciente_ctx(paciente: dict, historial: dict) -> str:
        if paciente.get("type") == "error":
            return paciente["message"]
        d = paciente.get("data", {})
        lines = [
            f"Paciente ID {d.get('id_paciente')}:",
            f"  Nombre:    {d.get('nombre_completo','-')}",
            f"  Cédula:    {d.get('cedula','-')}",
            f"  Teléfono:  {d.get('telefono','-')}",
            f"  Ocupación: {d.get('ocupacion','-')}",
            f"  Total consultas: {d.get('total_consultas',0)}",
        ]
        items = historial.get("items", [])
        if items:
            lines.append(f"\nHistorial ({len(items)} consultas):")
            for c in items[:10]:
                lines.append(
                    f"  [{c.get('fecha','-')}] Motivo: {(c.get('motivo_consulta') or '-')[:100]}"
                    f" | Enfermedad: {(c.get('enfermedad_actual') or '-')[:80]}"
                )
        else:
            lines.append("\nEste paciente no tiene consultas registradas.")
        return "\n".join(lines)

    @staticmethod
    def _fmt_consulta(c: dict) -> str:
        nombre = c.get("nombre_paciente") or ("ID " + str(c.get("id_paciente", "?")))
        motivo = (c.get("motivo_consulta") or "-")[:80]
        enf    = (c.get("enfermedad_actual") or "-")[:60]
        return f"  • [{nombre}] Motivo: {motivo} | Enfermedad: {enf}"
