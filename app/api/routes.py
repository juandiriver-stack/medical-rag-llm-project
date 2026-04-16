from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.agents.orchestrator import MedicalOrchestratorAgent
from app.api.schemas import ChatRequest, ChatResponse, RAGIndexResponse
from app.audio.tts_service import TTSService
from app.db.models import ConversationSession
from app.db.session import get_db
from app.rag.engine import rag_engine
from app.tools.consultas_tool import ConsultasTool

router = APIRouter()
tts_service = TTSService()


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "rag_index_built": rag_engine.is_built}


@router.post("/rag/index", response_model=RAGIndexResponse)
def build_rag_index(db: Session = Depends(get_db)) -> RAGIndexResponse:
    count = rag_engine.build_index(db)
    return RAGIndexResponse(indexed_consultas=count, message=f"Índice RAG construido con {count} consultas.")


@router.get("/rag/search")
def rag_search(q: str = Query(...), db: Session = Depends(get_db)) -> dict:
    if not rag_engine.is_built:
        raise HTTPException(status_code=400, detail="Índice RAG no construido.")
    return {"query": q, "results": rag_engine.search(q)}


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    """Pipeline Agente3 → Agente2 → Agente1 + TTS opcional."""
    orchestrator = MedicalOrchestratorAgent(db, llm_provider=payload.provider)
    result = orchestrator.handle(
        message=payload.message,
        session_id=payload.session_id or "default",
    )
    if payload.tts:
        result["tts"] = tts_service.synthesize(result["response"])
    return ChatResponse(**result)


@router.post("/chat/audio")
async def chat_audio(
    audio: UploadFile = File(...),
    provider: str = Query("claude"),
    session_id: str = Query("default"),
    tts: bool = Query(True),
    db: Session = Depends(get_db),
) -> dict:
    """Recibe audio → STT → pipeline 3 agentes → TTS."""
    audio_bytes = await audio.read()
    orchestrator = MedicalOrchestratorAgent(db, llm_provider=provider)
    result = orchestrator.handle(message="", session_id=session_id, audio_bytes=audio_bytes)
    if tts:
        result["tts"] = tts_service.synthesize(result["response"])
    return result


@router.get("/session/{session_id}/history")
def session_history(session_id: str, limit: int = Query(20, le=100), db: Session = Depends(get_db)) -> dict:
    rows = (
        db.query(ConversationSession)
        .filter(ConversationSession.session_id == session_id)
        .order_by(ConversationSession.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "session_id": session_id,
        "turns": [
            {"role": r.role, "content": r.content, "intent": r.intent,
             "source": r.source, "created_at": str(r.created_at)}
            for r in reversed(rows)
        ],
    }


@router.post("/tts")
def text_to_speech(payload: dict) -> dict:
    text = payload.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="Campo 'text' requerido.")
    return tts_service.synthesize(text)


@router.get("/consultas")
def list_consultas(limit: int = Query(20, le=100), db: Session = Depends(get_db)) -> dict:
    return {"data": ConsultasTool(db).list_consultas(limit=limit)}


@router.get("/consultas/search/")
def search_consultas(q: str = Query(...), limit: int = Query(10, le=50), db: Session = Depends(get_db)) -> dict:
    return {"data": ConsultasTool(db).search_consultas_by_motivo(q, limit=limit)}


@router.get("/consultas/{consulta_id}")
def get_consulta(consulta_id: int, db: Session = Depends(get_db)) -> dict:
    c = ConsultasTool(db).get_consulta(consulta_id)
    if not c:
        raise HTTPException(status_code=404, detail="Consulta no encontrada")
    return c


@router.get("/pacientes")
def list_pacientes(limit: int = Query(20, le=100), db: Session = Depends(get_db)) -> dict:
    return {"data": ConsultasTool(db).list_pacientes(limit=limit)}


@router.get("/pacientes/{paciente_id}")
def get_paciente(paciente_id: int, db: Session = Depends(get_db)) -> dict:
    p = ConsultasTool(db).get_paciente(paciente_id)
    if not p:
        raise HTTPException(status_code=404, detail="Paciente no encontrado")
    return p


@router.get("/pacientes/{paciente_id}/consultas")
def consultas_por_paciente(paciente_id: int, db: Session = Depends(get_db)) -> dict:
    return {"data": ConsultasTool(db).consultas_por_paciente(paciente_id)}


@router.get("/summary")
def summary(db: Session = Depends(get_db)) -> dict:
    return ConsultasTool(db).summary()


# ── Historia Clínica — extracción desde texto o audio ─────────────────────
@router.post("/hc/extract")
async def extract_hc(payload: dict, db: Session = Depends(get_db)) -> dict:
    """
    Extrae los campos de Historia Clínica desde texto de chat o transcripción de audio.
    NUNCA retorna error HTTP — siempre devuelve un JSON con ok=True o ok=False + fallback.

    Body:
        texto    (str)  : transcripción o mensaje de chat
        es_audio (bool) : True si el texto viene de audio (activa separación de voces)
    """
    from app.agents.agent_hc_extractor import HCExtractorAgent, segmentar_conversacion
    from app.core.config import settings

    texto    = payload.get("texto", "").strip()
    es_audio = bool(payload.get("es_audio", False))

    if not texto:
        # Retorna fallback mínimo en lugar de error
        return {"ok": False, "error": "texto vacío",
                "hc": _hc_fallback("", {}, "texto_vacio")}

    try:
        agente    = HCExtractorAgent(llm_provider=getattr(settings, "llm_provider", None))
        resultado = agente.extract(texto, es_audio=es_audio)
        return resultado
    except Exception as e:
        # Fallback: segmentar voces sin LLM y retornar JSON mínimo
        try:
            segs = segmentar_conversacion(texto)
        except Exception:
            segs = {"doctor": "", "paciente": texto[:300], "sin_clasificar": ""}
        return {"ok": False, "error": str(e),
                "hc": _hc_fallback(texto, segs, "error_llm")}


def _hc_fallback(texto: str, segs: dict, razon: str) -> dict:
    """HC mínima cuando el LLM no está disponible — siempre descargable."""
    return {
        "motivo_consulta": {
            "motivoConsulta":   texto[:300] if texto else None,
            "enfermedadActual": None
        },
        "estado_enfermedad": None,
        "recetas":           [],
        "examenes":          [],
        "revision_organos":  [],
        "examen_fisico":     [],
        "metadata": {
            "voz_doctor":      segs.get("doctor", "") or None,
            "voz_paciente":    segs.get("paciente", "") or None,
            "es_audio":        False,
            "confianza":       "baja",
            "campos_extraidos": 1 if texto else 0,
            "nota":            f"Extracción simplificada ({razon})"
        }
    }


@router.post("/hc/extract/audio")
async def extract_hc_audio(
    audio: UploadFile = File(...),
    db: Session = Depends(get_db)
) -> dict:
    """
    Recibe un archivo de audio, lo transcribe y extrae la HC con separación
    de voces doctor/paciente.

    Retorna el mismo JSON estructurado de la HC más la transcripción completa.
    """
    from app.agents.agent_hc_extractor import HCExtractorAgent
    from app.core.config import settings
    import tempfile, os

    # Guardar audio temporalmente
    suffix = os.path.splitext(audio.filename or "audio.wav")[1] or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name

    try:
        # Transcribir con Whisper si está disponible, sino retornar error claro
        try:
            import whisper
            model = whisper.load_model("base")
            result = model.transcribe(tmp_path, language="es")
            transcripcion = result["text"]
        except ImportError:
            raise HTTPException(
                status_code=501,
                detail="Whisper no instalado. Usa POST /api/hc/extract con la transcripción en texto."
            )

        agente    = HCExtractorAgent(llm_provider=getattr(settings, "llm_provider", None))
        resultado = agente.extract(transcripcion, es_audio=True)
        resultado["transcripcion"] = transcripcion
        return resultado

    finally:
        try: os.unlink(tmp_path)
        except Exception: pass
