"""
Agente HC Extractor — Historia Clínica con resolución de IDs desde tablas catálogo

Flujo:
  1. LLM extrae NOMBRES textuales desde la conversación
  2. CatalogResolver busca los IDs en las tablas de catálogo MySQL
  3. Retorna JSON con IDs resueltos listos para el frontend

Mapa de tablas catálogo:
  medicamentos                   → idMedicamentos        (recetas)
  hc_vias_administracion         → viasAdministracion_id (recetas)
  hc_unidadmedicamento           → unidad_id             (recetas)
  receta_pauta                   → pauta_id              (recetas)
  hc_tipos_examenes              → id_examen             (orden_examen)
  tipo_revision_organos_sistemas → tipoRevision_id       (revision_organos_sistemas)
  tipo_examen_fisico             → tipoExamen_id         (examen_fisico)
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "agent_hc_extractor.md"
HC_SYSTEM = _PROMPT_PATH.read_text(encoding="utf-8") if _PROMPT_PATH.exists() else ""

# ── Patrones de detección de voz ─────────────────────────────────────
_PATRONES_DOCTOR = [
    r"(?i)(soy\s+el?\s+dr\.?|soy\s+la?\s+dra\.?|doctor[a]?\s*:)",
    r"(?i)(le\s+voy\s+a\s+recetar|le\s+receto|prescrib)",
    r"(?i)(le\s+pido|solicito|orden[oa]\s+un[a]?|examen\s+de)",
    r"(?i)(al\s+examen|a\s+la\s+auscultaci[oó]n|a\s+la\s+palpaci[oó]n)",
    r"(?i)(diagn[oó]stico|impresi[oó]n\s+diagn[oó]stica)",
]
_PATRONES_PACIENTE = [
    r"(?i)(me\s+duele|tengo|siento|noto|me\s+molesta|sufro)",
    r"(?i)(desde\s+hace|hace\s+\d+\s+(d[ií]a|semana|mes|a[ñn]o))",
    r"(?i)(vine\s+porque|vengo\s+por|el\s+motivo|mi\s+problema)",
]


def detectar_voz(texto: str) -> str:
    t = texto.lower()
    doc = any(re.search(p, t) for p in _PATRONES_DOCTOR)
    pac = any(re.search(p, t) for p in _PATRONES_PACIENTE)
    if doc and pac: return "ambos"
    if doc: return "doctor"
    if pac: return "paciente"
    return "desconocido"


def segmentar_conversacion(texto: str) -> dict[str, str]:
    segmentos: dict[str, list] = {"doctor": [], "paciente": [], "sin_clasificar": []}
    patron = re.compile(
        r"(?i)(dr[a]?\.?\s+\w+\s*:|doctor[a]?\s*:|paciente\s*:|m[eé]dico[a]?\s*:|p\s*:|d\s*:)"
    )
    if patron.search(texto):
        partes = patron.split(texto)
        hablante = "sin_clasificar"
        for parte in partes:
            parte = parte.strip()
            if not parte: continue
            if patron.match(parte + ":") or patron.match(parte):
                etiqueta = parte.lower()
                if any(x in etiqueta for x in ["dr", "doctor", "médico", "d:"]):
                    hablante = "doctor"
                elif any(x in etiqueta for x in ["paciente", "p:"]):
                    hablante = "paciente"
            else:
                segmentos[hablante].append(parte)
    else:
        for oracion in re.split(r"[.!?]\s+", texto):
            voz = detectar_voz(oracion)
            if voz in ("doctor", "ambos"): segmentos["doctor"].append(oracion)
            elif voz == "paciente":        segmentos["paciente"].append(oracion)
            else:                          segmentos["sin_clasificar"].append(oracion)
    return {k: " ".join(v) for k, v in segmentos.items()}


# ══════════════════════════════════════════════════════════════════════
class CatalogResolver:
    """
    Resuelve nombres textuales → IDs consultando tablas catálogo en MySQL.
    Todas las búsquedas son insensibles a mayúsculas con fallback LIKE.
    """

    def __init__(self, db: "Session") -> None:
        self.db = db

    def _buscar(self, tabla: str, campo: str, valor: str | None,
                campo_id: str = "id") -> int | None:
        """Exacto insensible a mayúsculas → fallback LIKE."""
        if not valor:
            return None
        from sqlalchemy import text as sqlt
        try:
            row = self.db.execute(
                sqlt(f"SELECT {campo_id} FROM {tabla} "
                     f"WHERE LOWER({campo}) = LOWER(:v) LIMIT 1"),
                {"v": valor.strip()}
            ).fetchone()
            if row:
                return row[0]
            row = self.db.execute(
                sqlt(f"SELECT {campo_id} FROM {tabla} "
                     f"WHERE LOWER({campo}) LIKE LOWER(:v) LIMIT 1"),
                {"v": f"%{valor.strip()}%"}
            ).fetchone()
            return row[0] if row else None
        except Exception:
            return None

    def _buscar_multi(self, tabla: str, campos: list[str],
                      valor: str | None, campo_id: str = "id") -> int | None:
        """Busca en múltiples columnas (para receta_pauta)."""
        if not valor:
            return None
        from sqlalchemy import text as sqlt
        for campo in campos:
            try:
                row = self.db.execute(
                    sqlt(f"SELECT {campo_id} FROM {tabla} "
                         f"WHERE LOWER({campo}) LIKE LOWER(:v) LIMIT 1"),
                    {"v": f"%{valor.strip()}%"}
                ).fetchone()
                if row:
                    return row[0]
            except Exception:
                continue
        return None

    def resolver_receta(self, r: dict) -> dict:
        """
        Resuelve una receta:
          idMedicamentos_nombre    → medicamentos.id         → idMedicamentos
          viasAdministracion_nombre→ hc_vias_administracion.id → viasAdministracion_id
          unidad_nombre            → hc_unidadmedicamento.id → unidad_id
          pauta_nombre             → receta_pauta.id         → pauta_id
        """
        return {
            # IDs resueltos — listos para insertar en tabla recetas
            "idMedicamentos":        self._buscar(
                "medicamentos", "nombre", r.get("idMedicamentos_nombre")),
            "viasAdministracion_id": self._buscar(
                "hc_vias_administracion", "nombre", r.get("viasAdministracion_nombre")),
            "dosis":                 r.get("dosis"),
            "unidad_id":             self._buscar(
                "hc_unidadmedicamento", "nombre", r.get("unidad_nombre")),
            "pauta_id":              self._buscar_multi(
                "receta_pauta",
                ["intervalo", "frecuencia", "durante", "nombre"],
                r.get("pauta_nombre")),
            "dias":                  r.get("dias"),
            "total":                 r.get("total"),
            # Nombres originales como referencia para el frontend
            "_nombre_medicamento":   r.get("idMedicamentos_nombre"),
            "_via_administracion":   r.get("viasAdministracion_nombre"),
            "_unidad":               r.get("unidad_nombre"),
            "_pauta":                r.get("pauta_nombre"),
        }

    def resolver_examen(self, e: dict) -> dict:
        """
        Resuelve un examen:
          id_examen_nombre → hc_tipos_examenes.name → id_examen
          prioridad: RUTINA=2, URGENTE=1, CONTROL=3
        """
        return {
            "id_examen":       self._buscar(
                "hc_tipos_examenes", "name", e.get("id_examen_nombre")),
            "observaciones":   e.get("observaciones"),
            "prioridad":       e.get("prioridad"),
            "_nombre_examen":  e.get("id_examen_nombre"),
        }

    def resolver_revision_organo(self, o: dict) -> dict:
        """
        Resuelve revisión de órgano:
          tipoRevision_nombre → tipo_revision_organos_sistemas.Nombre → tipoRevision_id
        """
        return {
            "tipoRevision_id": self._buscar(
                "tipo_revision_organos_sistemas", "Nombre", o.get("tipoRevision_nombre")),
            "observacion":     o.get("observacion"),
            "_nombre_organo":  o.get("tipoRevision_nombre"),
        }

    def resolver_examen_fisico(self, ef: dict) -> dict:
        """
        Resuelve examen físico:
          tipoExamen_nombre → tipo_examen_fisico.nombre → tipoExamen_id
        """
        return {
            "tipoExamen_id":  self._buscar(
                "tipo_examen_fisico", "nombre", ef.get("tipoExamen_nombre")),
            "Observacion":    ef.get("Observacion"),
            "_nombre_region": ef.get("tipoExamen_nombre"),
        }

    def resolver_todo(self, hc: dict) -> dict:
        """Resuelve todos los IDs del JSON extraído por el LLM."""
        return {
            "motivo_consulta":  hc.get("motivo_consulta", {}),
            "recetas":          [self.resolver_receta(r)
                                 for r in hc.get("recetas", [])],
            "examenes":         [self.resolver_examen(e)
                                 for e in hc.get("examenes", [])],
            "revision_organos": [self.resolver_revision_organo(o)
                                 for o in hc.get("revision_organos", [])],
            "examen_fisico":    [self.resolver_examen_fisico(ef)
                                 for ef in hc.get("examen_fisico", [])],
            "metadata":         hc.get("metadata", {}),
        }


# ══════════════════════════════════════════════════════════════════════
class HCExtractorAgent:
    """
    Extrae HC desde conversación y resuelve IDs desde catálogo MySQL.
    """

    def __init__(self, llm_provider: str | None = None,
                 db: "Session | None" = None) -> None:
        self._llm_provider = llm_provider
        self._db           = db
        self._llm          = None

    def _get_llm(self):
        if self._llm is None:
            try:
                from app.core.llm_factory import get_llm
                self._llm = get_llm(self._llm_provider)
            except Exception:
                from app.core.llm_factory import NoLLM
                self._llm = NoLLM()
        return self._llm

    def extract(self, texto: str, es_audio: bool = False) -> dict:
        """
        Extrae HC y resuelve IDs.

        Returns:
            {
              "ok": true,
              "hc": { ...campos con IDs resueltos... },
              "hc_raw": { ...nombres textuales sin resolver (referencia)... }
            }
        """
        segmentos = segmentar_conversacion(texto)
        prompt    = self._build_prompt(texto, segmentos, es_audio)

        llm = self._get_llm()
        from app.core.llm_factory import NoLLM
        if isinstance(llm, NoLLM):
            return self._hc_vacia(texto, segmentos, "sin-llm")

        try:
            respuesta = llm.invoke(prompt, system=HC_SYSTEM)
            hc_raw    = self._parsear_json(respuesta)

            # Resolver IDs desde tablas catálogo
            if self._db is not None:
                resolver = CatalogResolver(self._db)
                hc_final = resolver.resolver_todo(hc_raw)
            else:
                # Sin BD: retorna nombres sin resolver (dev/test)
                hc_final = hc_raw

            hc_final["metadata"] = {
                **hc_final.get("metadata", {}),
                "voz_doctor":      segmentos["doctor"][:300] or None,
                "voz_paciente":    segmentos["paciente"][:300] or None,
                "sin_clasificar":  segmentos["sin_clasificar"][:200] or None,
                "es_audio":        es_audio,
                "confianza":       self._calcular_confianza(hc_raw),
                "campos_extraidos": self._contar_campos(hc_raw),
                "ids_resueltos":   self._db is not None,
            }
            return {"ok": True, "hc": hc_final, "hc_raw": hc_raw}

        except Exception as e:
            return {"ok": False, "error": str(e),
                    "hc": self._hc_vacia(texto, segmentos, "error")["hc"]}

    def _build_prompt(self, texto: str, segmentos: dict,
                      es_audio: bool) -> str:
        fuente = "transcripción de audio" if es_audio else "mensaje de chat"
        partes = [
            f"Analiza esta {fuente} y extrae los campos de la Historia Clínica.\n",
            f"CONVERSACIÓN COMPLETA:\n{texto}\n",
        ]
        if segmentos["doctor"]:
            partes.append(f"FRAGMENTOS DEL DOCTOR:\n{segmentos['doctor']}\n")
        if segmentos["paciente"]:
            partes.append(f"FRAGMENTOS DEL PACIENTE:\n{segmentos['paciente']}\n")
        partes.append(
            "IMPORTANTE: para campos que requieren ID de catálogo, "
            "extrae el NOMBRE TEXTUAL exactamente como se menciona en la conversación. "
            "Extrae en el JSON exacto indicado. Usa null si no se menciona."
        )
        return "\n".join(partes)

    @staticmethod
    def _parsear_json(texto: str) -> dict:
        limpio = re.sub(r"```(?:json)?\s*", "", texto).replace("```", "").strip()
        try:
            return json.loads(limpio)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", limpio, re.DOTALL)
            if m:
                return json.loads(m.group())
            raise ValueError(f"No se pudo parsear JSON: {limpio[:200]}")

    @staticmethod
    def _hc_vacia(texto: str, segmentos: dict, razon: str) -> dict:
        return {
            "ok": False, "razon": razon,
            "hc": {
                "motivo_consulta": {"motivoConsulta": None, "enfermedadActual": None},
                "recetas": [], "examenes": [],
                "revision_organos": [], "examen_fisico": [],
                "metadata": {
                    "voz_doctor":      segmentos.get("doctor"),
                    "voz_paciente":    segmentos.get("paciente"),
                    "es_audio":        False,
                    "confianza":       "baja",
                    "campos_extraidos": 0,
                    "ids_resueltos":   False,
                }
            }
        }

    @staticmethod
    def _calcular_confianza(hc: dict) -> str:
        score = 0
        mc = hc.get("motivo_consulta", {})
        if mc.get("motivoConsulta"):   score += 2
        if mc.get("enfermedadActual"): score += 2
        if hc.get("recetas"):          score += 3
        if hc.get("examenes"):         score += 2
        if hc.get("revision_organos"): score += 1
        if hc.get("examen_fisico"):    score += 1
        if score >= 7: return "alta"
        if score >= 3: return "media"
        return "baja"

    @staticmethod
    def _contar_campos(hc: dict) -> int:
        count = 0
        mc = hc.get("motivo_consulta", {})
        if mc.get("motivoConsulta"):   count += 1
        if mc.get("enfermedadActual"): count += 1
        count += len(hc.get("recetas", []))
        count += len(hc.get("examenes", []))
        count += len(hc.get("revision_organos", []))
        count += len(hc.get("examen_fisico", []))
        return count
