"""
Agente HC Extractor — Historia Clínica con resolución de IDs desde tablas catálogo

Flujo:
  1. LLM extrae NOMBRES textuales desde la conversación
  2. CatalogResolver busca los IDs en las tablas de catálogo MySQL
  3. Retorna JSON con IDs resueltos listos para el frontend

Mapa de tablas catálogo:
  medicamentos                   → idMedicamentos (PK)   (recetas)
  hc_vias_administracion         → id                    (recetas)
  hc_unidadmedicamento           → id                    (recetas)
  receta_pauta                   → id                    (recetas)
  hc_tipos_examenes              → id                    (orden_examen)
  tipo_revision_organos_sistemas → id                    (revision_organos_sistemas)
  tipo_examen_fisico             → id                    (examen_fisico)
"""
from __future__ import annotations
import difflib
import json
import re
import unicodedata
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


def _anonimizar(texto: str) -> str:
    """
    Enmascara cédulas ecuatorianas y números de teléfono en el texto
    antes de enviarlo al LLM externo. Solo aplica al texto hacia el LLM;
    lo que ve el médico en pantalla no se modifica.

    Patrones cubiertos:
      - Cédula: 10 dígitos seguidos (ej: 0921097408 → 092****408)
      - Teléfono fijo: 7-8 dígitos (ej: 2345678 → 23****78)
      - Teléfono móvil: 10 dígitos empezando en 09 (ej: 0991234567 → 099****567)
    """
    if not texto:
        return texto

    # Cédula ecuatoriana: 10 dígitos seguidos (no precedido por más dígitos)
    texto = re.sub(
        r'(?<!\d)(\d{3})\d{4}(\d{3})(?!\d)',
        lambda m: m.group(1) + "****" + m.group(2),
        texto
    )
    # Teléfono móvil: empieza en 09, 10 dígitos
    texto = re.sub(
        r'(?<!\d)(09\d)\d{4}(\d{3})(?!\d)',
        lambda m: m.group(1) + "****" + m.group(2),
        texto
    )
    return texto


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


# ── Normalización y matching ──────────────────────────────────────────
def _norm(s: str) -> str:
    """Minúsculas, sin acentos, slashes/guiones → espacio, espacios colapsados."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(s))
    # quitar acentos y reemplazar chars no-alfanuméricos por espacio
    chars = []
    for c in nfkd:
        if unicodedata.combining(c):
            continue
        if c.isalnum():
            chars.append(c)
        else:
            chars.append(" ")
    return " ".join("".join(chars).lower().split())

_PRIORIDAD: dict[str, int] = {"URGENTE": 1, "RUTINA": 2, "CONTROL": 3}

# Alias: término del LLM → variantes a buscar en catálogo (ya normalizadas)
_ALIAS_QUERY: dict[str, list[str]] = {
    # Exámenes de laboratorio
    "pcr":                          ["pcr", "proteina c reactiva"],
    "bh":                           ["bh", "biometria hematica", "hemograma"],
    "hemograma":                    ["hemograma", "biometria hematica", "bh"],
    "biometria hematica":           ["biometria hematica", "hemograma", "bh"],
    "rx":                           ["rx", "radiografia", "rayos x"],
    "eco":                          ["eco", "ecografia", "ultrasonido"],
    "tac":                          ["tac", "tomografia", "tomografia computarizada"],
    "rmn":                          ["rmn", "resonancia", "resonancia magnetica"],
    "ekg":                          ["ekg", "ecg", "electrocardiograma"],
    "ecg":                          ["ecg", "ekg", "electrocardiograma"],
    # Revisión de órganos — mapea nombres del LLM → nombres exactos en la DB
    "digestivo gastrointestinal":   ["digestivo", "digestivo gastrointestinal", "gastrointestinal"],
    "digestivo":                    ["digestivo", "digestivo gastrointestinal"],
    "gastrointestinal":             ["digestivo", "gastrointestinal", "digestivo gastrointestinal"],
    "urinario renal":               ["urinario", "renal", "urinario renal"],
    "urinario":                     ["urinario", "urinario renal", "renal"],
    "renal":                        ["urinario", "renal"],
    "neurologico":                  ["nervioso", "neurologico", "neurológico"],
    "nervioso":                     ["nervioso", "neurologico"],
    "cardiovascular":               ["cardiovascular", "cardio vascular"],
    "cardio vascular":              ["cardio vascular", "cardiovascular"],
    "musculo esqueletico":          ["musculo esqueletico", "musculo esquelétic", "esqueletico"],
    "musculoesqueletico":           ["musculo esqueletico", "esqueletico"],
    "hematologico":                 ["hemo linfatico", "hematologico", "hemo"],
    "hemo linfatico":               ["hemo linfatico", "hematologico"],
    "hematolinfatico":              ["hemo linfatico", "hematologico"],
    "ginecologico reproductivo":    ["ginecologico", "reproductivo", "genital"],
    "ginecologico":                 ["ginecologico", "genital", "reproductivo"],
    "psiquiatrico mental":          ["psiquiatrico", "mental"],
    "psiquiatrico":                 ["psiquiatrico", "mental"],
    "endocrino":                    ["endocrino"],
    "dermatologico":                ["dermatologico", "piel"],
    "oftalmologico":                ["oftalmologico", "organos de los sentidos"],
    "otorrinolaringologico":        ["otorrinolaringologico", "organos de los sentidos"],
    "respiratorio":                 ["respiratorio"],
}


# ══════════════════════════════════════════════════════════════════════
class CatalogResolver:
    """
    Resuelve nombres textuales → IDs en tablas catálogo MySQL.
    Usa normalización NFKD (sin acentos) + 5 niveles de matching.
    """

    def __init__(self, db: "Session") -> None:
        self.db = db

    # ── Carga de tabla ───────────────────────────────────────────────

    def _load(self, tabla: str, pk: str, campo: str) -> list[tuple]:
        from sqlalchemy import text as sqlt
        try:
            return self.db.execute(
                sqlt(f"SELECT {pk}, {campo} FROM {tabla}")
            ).fetchall()
        except Exception:
            pass
        # Fallback: auto-detectar PK con SHOW COLUMNS (maneja tablas con PK no estándar)
        try:
            cols = self.db.execute(sqlt(f"SHOW COLUMNS FROM {tabla}")).fetchall()
            pk_auto = cols[0][0]  # primera columna = PK generalmente
            return self.db.execute(
                sqlt(f"SELECT {pk_auto}, {campo} FROM {tabla}")
            ).fetchall()
        except Exception:
            return []

    # ── Matching multinivel ──────────────────────────────────────────

    def _match(self, rows: list[tuple], valor: str) -> int | None:
        """Exact → contains → reverse-contains → word-overlap → difflib."""
        if not valor or not rows:
            return None
        queries = _ALIAS_QUERY.get(_norm(valor), [_norm(valor)])

        for vn in queries:
            if not vn:
                continue

            # 1. Exacto normalizado
            for pk, nombre in rows:
                if nombre and _norm(nombre) == vn:
                    return pk

            # 2. valor contenido en nombre del catálogo
            for pk, nombre in rows:
                if nombre and vn in _norm(nombre):
                    return pk

            # 3. nombre del catálogo contenido en valor (nombres cortos)
            for pk, nombre in rows:
                if nombre:
                    nn = _norm(nombre)
                    if nn and len(nn) >= 3 and nn in vn:
                        return pk

            # 4. Solapamiento de palabras (≥ 50 %)
            vwords = set(vn.split())
            best_pk, best_score = None, 0.0
            for pk, nombre in rows:
                if not nombre:
                    continue
                nwords = set(_norm(nombre).split())
                common = vwords & nwords
                if not common:
                    continue
                score = len(common) / max(len(vwords), len(nwords))
                if score > best_score:
                    best_score, best_pk = score, pk
            if best_score >= 0.5:
                return best_pk

            # 5. difflib ratio (≥ 0.75)
            best_pk, best_ratio = None, 0.0
            for pk, nombre in rows:
                if not nombre:
                    continue
                r = difflib.SequenceMatcher(None, vn, _norm(nombre)).ratio()
                if r > best_ratio:
                    best_ratio, best_pk = r, pk
            if best_ratio >= 0.75:
                return best_pk

        return None

    # ── Búsqueda en tabla ────────────────────────────────────────────

    def _buscar(self, tabla: str, campo: str, valor: str | None,
                campo_id: str = "id") -> int | None:
        if not valor:
            return None
        rows = self._load(tabla, campo_id, campo)
        return self._match(rows, valor)

    def _buscar_multi(self, tabla: str, campos: list[str],
                      valor: str | None, campo_id: str = "id") -> int | None:
        """Busca contra múltiples columnas de texto (útil para receta_pauta)."""
        if not valor:
            return None
        for campo in campos:
            rows = self._load(tabla, campo_id, campo)
            if rows:
                result = self._match(rows, valor)
                if result is not None:
                    return result
        return None

    # ── Resolución por sección ───────────────────────────────────────

    def resolver_receta(self, r: dict) -> dict:
        return {
            "idMedicamentos":        self._buscar(
                "medicamentos", "nombre", r.get("idMedicamentos_nombre"),
                campo_id="idMedicamentos"),          # PK real de la tabla
            "viasAdministracion_id": self._buscar(
                "hc_vias_administracion", "nombre", r.get("viasAdministracion_nombre")),
            "dosis":                 r.get("dosis"),
            "unidad_id":             self._buscar(
                "hc_unidadmedicamento", "nombre", r.get("unidad_nombre")),
            "pauta_id":              self._buscar_multi(
                "receta_pauta",
                ["intervalo", "frecuencia", "durante", "nombre", "descripcion", "pauta"],
                r.get("pauta_nombre")),
            "dias":                  r.get("dias"),
            "total":                 r.get("total"),
            "lateralidad":           r.get("lateralidad"),
            "_nombre_medicamento":   r.get("idMedicamentos_nombre"),
            "_via_administracion":   r.get("viasAdministracion_nombre"),
            "_unidad":               r.get("unidad_nombre"),
            "_pauta":                r.get("pauta_nombre"),
        }

    def resolver_examen(self, e: dict) -> dict:
        tipo = (e.get("tipo") or "laboratorio").lower()
        imagen = 1 if tipo == "imagen" else 0
        p = e.get("prioridad", "RUTINA")
        prioridad = _PRIORIDAD.get(str(p).upper(), p if isinstance(p, int) else 2)
        return {
            "id_examen":            self._buscar(
                "hc_tipos_examenes", "name", e.get("id_examen_nombre")),
            "observaciones":        e.get("observaciones"),
            "tipo":                 tipo,
            "imagen":               imagen,
            "prioridad":            prioridad,
            "paciente_contaminado": int(e.get("paciente_contaminado") or 0),
            "sedacion":             int(e.get("sedacion") or 0),
            "_nombre_examen":       e.get("id_examen_nombre"),
        }

    def resolver_revision_organo(self, o: dict) -> dict:
        return {
            "tipoRevision_id": self._buscar(
                "tipo_revision_organos_sistemas", "Nombre", o.get("tipoRevision_nombre")),
            "observacion":     o.get("observacion"),
            "_nombre_organo":  o.get("tipoRevision_nombre"),
        }

    def resolver_examen_fisico(self, ef: dict) -> dict:
        return {
            "tipoExamen_id":  self._buscar(
                "tipo_examen_fisico", "nombre", ef.get("tipoExamen_nombre")),
            "Observacion":    ef.get("Observacion"),
            "_nombre_region": ef.get("tipoExamen_nombre"),
        }

    def resolver_todo(self, hc: dict) -> dict:
        return {
            "motivo_consulta":   hc.get("motivo_consulta", {}),
            "estado_enfermedad": hc.get("estado_enfermedad"),
            "recetas":           [self.resolver_receta(r)
                                  for r in hc.get("recetas", [])],
            "examenes":          [self.resolver_examen(e)
                                  for e in hc.get("examenes", [])],
            "revision_organos":  [self.resolver_revision_organo(o)
                                  for o in hc.get("revision_organos", [])],
            "examen_fisico":     [self.resolver_examen_fisico(ef)
                                  for ef in hc.get("examen_fisico", [])],
            "metadata":          hc.get("metadata", {}),
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
            # Anonimizar cédulas y teléfonos antes de enviar al LLM externo
            prompt_anonimizado = _anonimizar(prompt)
            respuesta = llm.invoke(prompt_anonimizado, system=HC_SYSTEM)
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
