"""
Agente SQL — Especialista en consultas directas a MySQL

Responsabilidad única: obtener datos exactos de la BD.
  - Búsqueda por ID, cédula, nombre
  - Historial de consultas de un paciente
  - Estadísticas globales del sistema

No hace búsquedas semánticas. No redacta respuestas.
Retorna siempre JSON estructurado.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.tools.consultas_tool import ConsultasTool

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "agent_sql.md"
SQL_AGENT_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8") if _PROMPT_PATH.exists() else ""

# ── Detectores de entidad ─────────────────────────────────────────────────
def _extraer_id(text: str) -> int | None:
    patrones = [
        r"(?:paciente\s+(?:id|#|n[uú]mero)?\s*[:=]?\s*)(\d{3,7})",
        r"(?:id\s*[:=]\s*)(\d{3,7})",
        r"(?:historial\s+(?:del\s+)?(?:paciente\s+)?(?:id\s+)?)(\d{3,7})",
        r"(?:#\s*)(\d{3,7})",
        r"\b(\d{5,7})\b",
    ]
    for p in patrones:
        m = re.search(p, text.lower())
        if m:
            return int(m.group(1))
    return None


def _extraer_cedula(text: str) -> str | None:
    patrones = [
        r"(?:c[eé]dula|identificaci[oó]n|ci|ruc)\s*[:=]?\s*(\d{6,13})",
        r"\b(\d{10})\b",
    ]
    for p in patrones:
        m = re.search(p, text.lower())
        if m:
            return m.group(1)
    return None


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
        if nombre == antes:
            break
    nombre = re.sub(r"\s+(por favor|gracias|porfavor|ok).*$", "", nombre, flags=re.IGNORECASE)
    nombre = nombre.strip(".,?¿!¡ \t")
    return nombre if len(nombre) >= 3 else None


def _es_nombre_mayusculas(text: str) -> bool:
    triggers = [
        r"consultas?\s+(?:de[l]?\s+)?(?:paciente\s+)?[A-ZÁÉÍÓÚÑ]{2,}",
        r"historial\s+(?:cl[ií]nico\s+)?(?:de[l]?\s+)?(?:paciente\s+)?[A-ZÁÉÍÓÚÑ]{2,}",
        r"resumen\s+(?:de\s+)?(?:consultas?\s+)?(?:de[l]?\s+)?(?:paciente\s+)?[A-ZÁÉÍÓÚÑ]{2,}",
        r"paciente\s+[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ]+\s+[A-ZÁÉÍÓÚÑ]",
        r"buscar?\s+(?:a\s+)?(?:paciente\s+)?[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ]+",
        r"datos?\s+(?:de[l]?\s+)?(?:paciente\s+)?[A-ZÁÉÍÓÚÑ]{2,}",
    ]
    for p in triggers:
        if re.search(p, text):
            return True
    palabras = text.strip().split()
    if len(palabras) >= 2 and sum(1 for p in palabras if p.isupper() and len(p) > 1) >= 2:
        return True
    return False


class SQLAgent:
    """
    Agente especializado en consultas SQL directas.
    Detecta el tipo de búsqueda y ejecuta la herramienta correspondiente.
    """

    def __init__(self, tool: "ConsultasTool", db: "Session") -> None:
        self.tool = tool
        self.db   = db

    def execute(self, intent: str, text: str, keywords: list) -> dict:
        """
        Punto de entrada. Determina qué consulta SQL ejecutar según:
        - Intent: estadisticas | buscar_paciente
        - Entidad detectada: ID | cédula | nombre | lista general
        """
        # ── Ruta: estadisticas ────────────────────────────────────────
        if intent == "estadisticas":
            return self._estadisticas(text)

        # ── Ruta: buscar_paciente ─────────────────────────────────────
        pid = _extraer_id(text)
        if pid:
            return self._por_id(pid, text)

        ced = _extraer_cedula(text)
        if ced:
            return self._por_cedula(ced, text)

        if _es_nombre_mayusculas(text):
            nombre = _extraer_nombre(text)
            if nombre:
                return self._por_nombre(nombre, text)

        # Listar pacientes si no hay entidad específica
        return self._listar_pacientes(text)

    # ── Estadísticas ──────────────────────────────────────────────────
    def _estadisticas(self, text: str) -> dict:
        data = self.tool.summary()
        ctx = (
            f"Estadísticas del sistema San Marcos Guayaquil:\n"
            f"  Total consultas registradas: {data['total_consultas']}\n"
            f"  Total pacientes registrados:  {data['total_pacientes']}\n"
            f"  Consultas con motivo documentado: {data['con_motivo_registrado']}\n"
            f"  Tabla paciente activa: {'Sí' if data['tabla_paciente_activa'] else 'No'}"
        )
        return {
            "agent":      "sql",
            "tools_used": ["sql_summary"],
            "raw_data":   data,
            "context":    ctx,
            "intent":     "estadisticas",
        }

    # ── Búsqueda por ID ───────────────────────────────────────────────
    def _por_id(self, pid: int, text: str) -> dict:
        paciente  = self.tool.get_paciente(pid)
        historial = self.tool.consultas_por_paciente(pid)
        return {
            "agent":      "sql",
            "tools_used": ["sql_pacientes", "sql_historial"],
            "raw_data":   {"paciente": paciente, "historial": historial},
            "context":    self._fmt_paciente(paciente, historial),
            "intent":     "buscar_paciente",
        }

    # ── Búsqueda por cédula ───────────────────────────────────────────
    def _por_cedula(self, cedula: str, text: str) -> dict:
        from sqlalchemy import text as sqlt
        try:
            row = self.db.execute(
                sqlt("SELECT ID_PACIENTE FROM paciente WHERE IDENTIFICACION = :c LIMIT 1"),
                {"c": cedula}
            ).fetchone()
            if row:
                return self._por_id(row.ID_PACIENTE, text)
        except Exception:
            pass
        return {
            "agent":      "sql",
            "tools_used": ["sql_pacientes"],
            "raw_data":   {},
            "context":    f"No se encontró paciente con cédula {cedula}.",
            "intent":     "buscar_paciente",
        }

    # ── Búsqueda por nombre ───────────────────────────────────────────
    def _por_nombre(self, nombre: str, text: str) -> dict:
        from sqlalchemy import text as sqlt
        try:
            palabras = nombre.upper().split()
            if len(palabras) >= 2:
                cond   = " AND ".join(
                    f"CONCAT(NOMBRES,' ',APELLIDOS) LIKE :p{i}"
                    for i in range(len(palabras))
                )
                params = {f"p{i}": f"%{p}%" for i, p in enumerate(palabras)}
            else:
                cond   = "CONCAT(NOMBRES,' ',APELLIDOS) LIKE :p0"
                params = {"p0": f"%{palabras[0]}%"}

            rows = self.db.execute(
                sqlt(f"SELECT ID_PACIENTE FROM paciente WHERE {cond} LIMIT 5"),
                params
            ).fetchall()

            if not rows:
                return {
                    "agent":      "sql",
                    "tools_used": ["sql_pacientes"],
                    "raw_data":   {},
                    "context":    f"No se encontró paciente con nombre '{nombre}'.",
                    "intent":     "buscar_paciente",
                }
            if len(rows) == 1:
                return self._por_id(rows[0].ID_PACIENTE, text)

            # Varios resultados → lista para elegir
            pacientes = [self.tool.get_paciente(r.ID_PACIENTE) for r in rows]
            lista = "\n".join(
                f"  • ID {p['data']['id_paciente']} — "
                f"{p['data'].get('nombre_completo','?')} | "
                f"Cédula: {p['data'].get('cedula','-')}"
                for p in pacientes if p.get("type") == "paciente_detail"
            )
            return {
                "agent":      "sql",
                "tools_used": ["sql_pacientes"],
                "raw_data":   {"pacientes": pacientes},
                "context":    (
                    f"Se encontraron {len(rows)} pacientes con '{nombre}':\n"
                    f"{lista}\n\nEscribe el ID para ver el historial completo."
                ),
                "intent":     "buscar_paciente",
            }
        except Exception as e:
            return {
                "agent":      "sql",
                "tools_used": ["sql_pacientes"],
                "raw_data":   {},
                "context":    f"Error al buscar por nombre: {e}",
                "intent":     "buscar_paciente",
            }

    # ── Listar pacientes ──────────────────────────────────────────────
    def _listar_pacientes(self, text: str) -> dict:
        data = self.tool.list_pacientes(limit=15)
        items = data.get("items", [])
        filas = "\n".join(
            f"  • ID {p.get('id_paciente')} — {p.get('nombre_completo','-')} "
            f"| Cédula: {p.get('cedula','-')}"
            for p in items
        )
        return {
            "agent":      "sql",
            "tools_used": ["sql_pacientes"],
            "raw_data":   data,
            "context":    f"Pacientes registrados ({data['count']}):\n{filas}",
            "intent":     "buscar_paciente",
        }

    # ── Formatter ─────────────────────────────────────────────────────
    @staticmethod
    def _fmt_paciente(paciente: dict, historial: dict) -> str:
        if paciente.get("type") == "error":
            return paciente["message"]
        d = paciente.get("data", {})
        lines = [
            f"Paciente ID {d.get('id_paciente')}:",
            f"  Nombre:    {d.get('nombre_completo', '-')}",
            f"  Cédula:    {d.get('cedula', '-')}",
            f"  Teléfono:  {d.get('telefono', '-')}",
            f"  Ocupación: {d.get('ocupacion', '-')}",
            f"  Total consultas: {d.get('total_consultas', 0)}",
        ]
        items = historial.get("items", [])
        if items:
            lines.append(f"\nHistorial ({len(items)} consultas — mostrando {min(10,len(items))}):")
            for c in items[:10]:
                fecha  = c.get("fecha", "-")
                motivo = (c.get("motivo_consulta")   or "-")[:100]
                enf    = (c.get("enfermedad_actual") or "-")[:80]
                lines.append(f"  [{fecha}] Motivo: {motivo} | Enfermedad: {enf}")
        else:
            lines.append("\nEste paciente no tiene consultas registradas.")
        return "\n".join(lines)
