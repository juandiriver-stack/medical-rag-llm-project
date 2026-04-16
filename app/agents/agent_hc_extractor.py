"""
Agente HC Extractor — Historia Clínica desde conversación médica

Extrae campos estructurados de la HC desde:
  - Texto libre de chat
  - Transcripción de audio (con separación doctor vs paciente)

Campos basados en el Excel Campos_HC.xlsx:
  MOTIVO DE CONSULTA → consultas: motivoConsulta, enfermedadActual
  RECETAS            → recetas: producto, vía, dosis, unidad, frecuencia, días, total, lateralidad
  EXÁMENES           → orden_examen: id_examen, tipo, prioridad, observaciones, contaminado, sedación
  REVISIÓN ÓRGANOS   → revision_organos_sistemas: tipoRevision, observacion
  EXAMEN FÍSICO      → examen_fisico: tipoExamen, observacion
  ESTADO ENFERMEDAD  → doc_solicitud_procedimientos: estado_enfermedad (1=agudo, 2=crónico)

Separación de voz:
  Doctor  → prescripciones, órdenes, hallazgos clínicos
  Paciente → síntomas, motivo de consulta, historia personal
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "agent_hc_extractor.md"
HC_SYSTEM = _PROMPT_PATH.read_text(encoding="utf-8") if _PROMPT_PATH.exists() else ""

# ── Patrones de detección de voz ───────────────────────────────────────────
_PATRONES_DOCTOR = [
    r"(?i)(soy\s+el?\s+dr\.?|soy\s+la?\s+dra\.?|doctor[a]?\s*:)",
    r"(?i)(le\s+voy\s+a\s+recetar|le\s+receto|prescrib|te\s+voy\s+a\s+dar)",
    r"(?i)(le\s+pido|solicito|orden[oa]\s+un[a]?|examen\s+de)",
    r"(?i)(al\s+examen|a\s+la\s+auscultación|a\s+la\s+palpación|a\s+la\s+exploración)",
    r"(?i)(diagnóstico|diagnóstico\s+es|impresión\s+diagnóstica)",
    r"(?i)(tome|tomar|aplicar|administrar)\s+\d",
]

_PATRONES_PACIENTE = [
    r"(?i)(soy\s+el?\s+paciente|soy\s+la?\s+paciente)",
    r"(?i)(me\s+duele|tengo|siento|noto|me\s+molesta|sufro)",
    r"(?i)(desde\s+hace|hace\s+\d+\s+(día|semana|mes|año))",
    r"(?i)(vine\s+porque|vengo\s+por|el\s+motivo|mi\s+problema)",
    r"(?i)(no\s+puedo|me\s+cuesta|me\s+ha\s+costado)",
]

_INDICADORES_DOCTOR = [
    "dr.", "dra.", "doctor", "doctora", "médico", "médica",
    "le receto", "le voy a recetar", "le pido", "solicito",
    "al examen", "diagnóstico", "prescrib", "administr",
]

_INDICADORES_PACIENTE = [
    "me duele", "tengo", "siento", "desde hace", "vine porque",
    "vengo por", "me molesta", "sufro de", "mi problema",
    "no puedo", "me ha costado",
]


def detectar_voz(texto: str) -> str:
    """
    Detecta si un fragmento de texto fue dicho por el doctor o el paciente.
    Retorna: 'doctor' | 'paciente' | 'ambos' | 'desconocido'
    """
    t = texto.lower()
    es_doctor   = any(re.search(p, t) for p in _PATRONES_DOCTOR)
    es_paciente = any(re.search(p, t) for p in _PATRONES_PACIENTE)

    if es_doctor and es_paciente:
        return "ambos"
    if es_doctor:
        return "doctor"
    if es_paciente:
        return "paciente"
    return "desconocido"


def segmentar_conversacion(texto: str) -> dict[str, str]:
    """
    Divide la conversación en fragmentos atribuidos al doctor y al paciente.
    Busca patrones de turnos de habla y los clasifica.

    Retorna:
        {"doctor": "...", "paciente": "...", "sin_clasificar": "..."}
    """
    segmentos = {"doctor": [], "paciente": [], "sin_clasificar": []}

    # Patrones de turno explícito: "Doctor:", "Paciente:", "Dr. X:", etc.
    patron_turno = re.compile(
        r"(?i)(dr[a]?\.?\s+\w+\s*:|doctor[a]?\s*:|paciente\s*:|"
        r"médico[a]?\s*:|p\s*:|d\s*:)"
    )

    if patron_turno.search(texto):
        # Hay turnos explícitos — dividir por ellos
        partes = patron_turno.split(texto)
        hablante_actual = "sin_clasificar"
        for parte in partes:
            parte = parte.strip()
            if not parte:
                continue
            if patron_turno.match(parte + ":") or patron_turno.match(parte):
                # Es un marcador de hablante
                etiqueta = parte.lower()
                if any(x in etiqueta for x in ["dr", "doctor", "médico", "d:"]):
                    hablante_actual = "doctor"
                elif any(x in etiqueta for x in ["paciente", "p:"]):
                    hablante_actual = "paciente"
            else:
                segmentos[hablante_actual].append(parte)
    else:
        # Sin turnos explícitos — clasificar por contenido
        oraciones = re.split(r"[.!?]\s+", texto)
        for oracion in oraciones:
            voz = detectar_voz(oracion)
            if voz in ("doctor", "ambos"):
                segmentos["doctor"].append(oracion)
            elif voz == "paciente":
                segmentos["paciente"].append(oracion)
            else:
                segmentos["sin_clasificar"].append(oracion)

    return {
        "doctor":         " ".join(segmentos["doctor"]),
        "paciente":       " ".join(segmentos["paciente"]),
        "sin_clasificar": " ".join(segmentos["sin_clasificar"]),
    }


class HCExtractorAgent:
    """
    Extrae los campos de la Historia Clínica desde texto o audio.
    Retorna un JSON estructurado listo para el frontend.
    """

    def __init__(self, llm_provider: str | None = None) -> None:
        self._llm_provider = llm_provider
        self._llm = None

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
        Punto de entrada principal.

        Args:
            texto:    transcripción del audio o mensaje de chat
            es_audio: True si viene de audio (aplica segmentación de voz)

        Returns:
            dict con la HC estructurada + metadata de voces
        """
        # 1. Segmentación de voces (más importante en audio)
        segmentos = segmentar_conversacion(texto)

        # 2. Construir prompt contextual
        prompt = self._build_prompt(texto, segmentos, es_audio)

        # 3. Invocar LLM para extracción
        llm = self._get_llm()
        from app.core.llm_factory import NoLLM
        if isinstance(llm, NoLLM):
            return self._hc_vacia(texto, segmentos, "sin-llm")

        try:
            respuesta = llm.invoke(prompt, system=HC_SYSTEM)
            hc_json   = self._parsear_json(respuesta)

            # 4. Enriquecer con metadata de segmentación
            hc_json["metadata"] = {
                "voz_doctor":      segmentos["doctor"][:300] or None,
                "voz_paciente":    segmentos["paciente"][:300] or None,
                "sin_clasificar":  segmentos["sin_clasificar"][:200] or None,
                "es_audio":        es_audio,
                "confianza":       self._calcular_confianza(hc_json),
                "campos_extraidos": self._contar_campos(hc_json),
            }
            return {"ok": True, "hc": hc_json}

        except Exception as e:
            return {"ok": False, "error": str(e),
                    "hc": self._hc_vacia(texto, segmentos, "error")["hc"]}

    def _build_prompt(self, texto: str, segmentos: dict,
                      es_audio: bool) -> str:
        fuente = "transcripción de audio con voz del doctor y del paciente" if es_audio else "mensaje de chat médico"
        partes = [
            f"Analiza esta {fuente} y extrae los campos de la Historia Clínica.\n",
            f"CONVERSACIÓN COMPLETA:\n{texto}\n",
        ]
        if segmentos["doctor"]:
            partes.append(f"FRAGMENTOS DEL DOCTOR:\n{segmentos['doctor']}\n")
        if segmentos["paciente"]:
            partes.append(f"FRAGMENTOS DEL PACIENTE:\n{segmentos['paciente']}\n")
        partes.append("Extrae los campos en el JSON exacto indicado. null si no se menciona.")
        return "\n".join(partes)

    @staticmethod
    def _parsear_json(texto: str) -> dict:
        """Extrae el JSON de la respuesta del LLM limpiando markdown."""
        # Limpiar bloques ```json ... ```
        limpio = re.sub(r"```(?:json)?\s*", "", texto).replace("```", "").strip()
        try:
            return json.loads(limpio)
        except json.JSONDecodeError:
            # Intento 2: buscar el primer { ... }
            m = re.search(r"\{.*\}", limpio, re.DOTALL)
            if m:
                return json.loads(m.group())
            raise ValueError(f"No se pudo parsear JSON de la respuesta: {limpio[:200]}")

    @staticmethod
    def _hc_vacia(texto: str, segmentos: dict, razon: str) -> dict:
        """HC vacía cuando no hay LLM disponible."""
        return {
            "ok": False,
            "razon": razon,
            "hc": {
                "motivo_consulta": {"motivoConsulta": None, "enfermedadActual": None},
                "estado_enfermedad": None,
                "recetas": [],
                "examenes": [],
                "revision_organos": [],
                "examen_fisico": [],
                "metadata": {
                    "voz_doctor":     segmentos.get("doctor"),
                    "voz_paciente":   segmentos.get("paciente"),
                    "es_audio":       False,
                    "confianza":      "baja",
                    "campos_extraidos": 0,
                }
            }
        }

    @staticmethod
    def _calcular_confianza(hc: dict) -> str:
        """Estima la confianza según cuántos campos se extrajeron."""
        score = 0
        mc = hc.get("motivo_consulta", {})
        if mc.get("motivoConsulta"):  score += 2
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
        """Cuenta campos no nulos extraídos."""
        count = 0
        mc = hc.get("motivo_consulta", {})
        if mc.get("motivoConsulta"):   count += 1
        if mc.get("enfermedadActual"): count += 1
        if hc.get("estado_enfermedad") is not None: count += 1
        count += len(hc.get("recetas", []))
        count += len(hc.get("examenes", []))
        count += len(hc.get("revision_organos", []))
        count += len(hc.get("examen_fisico", []))
        return count
