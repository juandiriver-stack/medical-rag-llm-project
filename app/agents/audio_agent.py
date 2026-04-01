"""
Agente 3 — Audio Agent  (módulo independiente)
Prompt cargado desde: app/prompts/agent_audio.md
"""
from __future__ import annotations
import re, json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.observability import observer

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Cargar prompt desde .md
_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "agent_audio.md"
INTENT_SYSTEM = _PROMPT_PATH.read_text(encoding="utf-8") if _PROMPT_PATH.exists() else ""

_RULES = {
    "estadisticas":    ["resumen", "estadística", "total", "cuántos", "cuantos", "summary"],
    "buscar_paciente": ["paciente", "listar pacientes", "id:", "paciente id", "historial"],
    "consulta_medica": ["consulta", "consultas", "enfermedad", "motivo", "examen",
                        "diagnóstico", "tratamiento", "síntoma", "dolor", "frecuente"],
}

def _intent_por_reglas(text: str) -> dict:
    t = text.lower()
    for intent, words in _RULES.items():
        if any(w in t for w in words):
            return {"intent": intent, "keywords": [w for w in t.split() if len(w)>3][:4], "clean_text": text}
    return {"intent": "general", "keywords": [], "clean_text": text}


class AudioAgent:
    """Agente 3: STT + normalización + extracción de intención."""

    def __init__(self, db: "Session | None" = None, llm_provider: str | None = None) -> None:
        self.db = db
        self._llm_provider = llm_provider
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            try:
                from app.core.llm_factory import get_llm
                self._llm = get_llm(self._llm_provider)
            except Exception:
                pass
        return self._llm

    def process(self, raw_input: str, session_id: str = "default",
                audio_bytes: bytes | None = None, trace=None) -> dict:
        source = "text"
        if audio_bytes:
            raw_input = self._transcribe(audio_bytes)
            source = "audio"

        clean       = self._normalize(raw_input)
        intent_data = self._extract_intent(clean, trace)

        if self.db:
            self._log_turn_safe(session_id, raw_input, intent_data, source)

        return {
            "text":       intent_data.get("clean_text", clean),
            "raw_text":   raw_input,
            "intent":     intent_data.get("intent", "general"),
            "keywords":   intent_data.get("keywords", []),
            "session_id": session_id,
            "source":     source,
            "timestamp":  datetime.utcnow().isoformat(),
        }

    def _transcribe(self, audio_bytes: bytes) -> str:
        provider = settings.stt_provider.lower()
        if provider == "whisper":
            try:
                import tempfile, whisper
                model = whisper.load_model(settings.whisper_model)
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    f.write(audio_bytes); tmp = f.name
                return model.transcribe(tmp, language="es").get("text", "")
            except Exception as e:
                return f"[whisper error: {e}]"
        if provider == "google":
            try:
                from google.cloud import speech
                client = speech.SpeechClient()
                audio  = speech.RecognitionAudio(content=audio_bytes)
                config = speech.RecognitionConfig(
                    encoding=speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
                    language_code="es-EC")
                resp = client.recognize(config=config, audio=audio)
                return " ".join(r.alternatives[0].transcript for r in resp.results)
            except Exception as e:
                return f"[google stt error: {e}]"
        return ""

    @staticmethod
    def _normalize(text: str) -> str:
        text = text.strip()
        text = re.sub(r"\b(eh|um|uhm|este|bueno|pues|o sea|mmm)\b", "", text, flags=re.I)
        return re.sub(r"\s{2,}", " ", text).strip()

    def _extract_intent(self, text: str, trace=None) -> dict:
        llm = self._get_llm()
        if llm is None:
            return _intent_por_reglas(text)
        try:
            from app.core.llm_factory import NoLLM
            if isinstance(llm, NoLLM):
                return _intent_por_reglas(text)
            raw = llm.invoke(f"Mensaje: {text}", system=INTENT_SYSTEM)
            raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            result = json.loads(raw)
            if "intent" not in result:
                raise ValueError("sin intent")
            return result
        except Exception:
            return _intent_por_reglas(text)

    def _log_turn_safe(self, session_id, text, intent_data, source):
        try:
            from app.db.models import ConversationSession
            self.db.add(ConversationSession(
                session_id=session_id, role="user", content=text,
                intent=intent_data.get("intent"), source=source))
            self.db.commit()
        except Exception:
            try: self.db.rollback()
            except Exception: pass
