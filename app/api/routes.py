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
