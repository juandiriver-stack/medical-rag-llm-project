"""
Servicio TTS (Text-to-Speech)

Modos:
  - browser  → responde con texto, el frontend usa SpeechSynthesis API
  - openai   → genera audio mp3 via OpenAI TTS
  - google   → Google Cloud TTS
"""
from __future__ import annotations
import base64
from app.core.config import settings


class TTSService:
    def synthesize(self, text: str) -> dict:
        """Retorna dict con mode y opcionalmente audio_b64."""
        if not settings.enable_tts:
            return {"mode": "disabled", "text": text}

        provider = settings.tts_provider.lower()

        if provider == "openai":
            return self._openai_tts(text)
        if provider == "google":
            return self._google_tts(text)
        # browser mode — el frontend lo reproduce
        return {"mode": "browser", "text": text, "voice": settings.tts_voice}

    def _openai_tts(self, text: str) -> dict:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key)
            response = client.audio.speech.create(
                model="tts-1",
                voice="alloy",
                input=text[:4096],
            )
            audio_b64 = base64.b64encode(response.content).decode()
            return {"mode": "openai", "audio_b64": audio_b64, "text": text}
        except Exception as e:
            return {"mode": "browser", "text": text, "error": str(e)}

    def _google_tts(self, text: str) -> dict:
        try:
            from google.cloud import texttospeech
            client = texttospeech.TextToSpeechClient()
            synthesis_input = texttospeech.SynthesisInput(text=text[:5000])
            voice = texttospeech.VoiceSelectionParams(
                language_code="es-EC",
                ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3
            )
            response = client.synthesize_speech(
                input=synthesis_input, voice=voice, audio_config=audio_config
            )
            audio_b64 = base64.b64encode(response.audio_content).decode()
            return {"mode": "google", "audio_b64": audio_b64, "text": text}
        except Exception as e:
            return {"mode": "browser", "text": text, "error": str(e)}
