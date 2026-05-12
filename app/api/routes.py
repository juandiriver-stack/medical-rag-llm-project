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




# ── LLM-as-a-Judge — Evaluación y comparación de modelos ─────────────
@router.post("/judge/evaluate")
async def judge_evaluate(payload: dict, db: Session = Depends(get_db)) -> dict:
    """
    Evalúa la calidad de una respuesta médica con LLM-as-a-Judge.

    Body:
        pregunta        (str): consulta del usuario
        contexto        (str): contexto RAG/SQL usado
        respuesta       (str): respuesta a evaluar
        modelo_evaluado (str): nombre del LLM que generó la respuesta
        judge_provider  (str): LLM juez (opcional, por defecto el opuesto)

    Retorna scores 1-5 en: relevancia, fidelidad, completitud,
    claridad, seguridad_clinica + score_promedio + justificación
    """
    from app.agents.agent_judge import JudgeAgent

    pregunta        = payload.get("pregunta", "").strip()
    contexto        = payload.get("contexto", "").strip()
    respuesta       = payload.get("respuesta", "").strip()
    modelo_evaluado = payload.get("modelo_evaluado", "desconocido")
    judge_provider  = payload.get("judge_provider", None)

    if not pregunta or not respuesta:
        raise HTTPException(status_code=400,
            detail="Se requieren 'pregunta' y 'respuesta'")

    judge  = JudgeAgent(judge_provider=judge_provider)
    result = judge.evaluate(
        pregunta=pregunta, contexto=contexto,
        respuesta=respuesta, modelo_evaluado=modelo_evaluado
    )
    return result


@router.post("/judge/compare")
async def judge_compare(payload: dict, db: Session = Depends(get_db)) -> dict:
    """
    Compara respuestas de múltiples LLMs para la misma pregunta.
    Útil para el benchmark de selección de modelo solicitado por el tutor.

    Body:
        pregunta   (str):  consulta del usuario
        contexto   (str):  contexto RAG/SQL (mismo para todos)
        respuestas (dict): {"claude": "resp1", "openai": "resp2", "ollama": "resp3"}
        judge_provider (str): LLM que actúa como juez

    Retorna: evaluación por modelo + ranking + ganador + resumen
    """
    from app.agents.agent_judge import JudgeAgent

    pregunta       = payload.get("pregunta", "").strip()
    contexto       = payload.get("contexto", "").strip()
    respuestas     = payload.get("respuestas", {})
    judge_provider = payload.get("judge_provider", "openai")

    if not pregunta or not respuestas:
        raise HTTPException(status_code=400,
            detail="Se requieren 'pregunta' y 'respuestas'")

    judge  = JudgeAgent(judge_provider=judge_provider)
    result = judge.compare_models(
        pregunta=pregunta, contexto=contexto, respuestas=respuestas
    )
    return result


@router.post("/judge/benchmark")
async def judge_benchmark(payload: dict, db: Session = Depends(get_db)) -> dict:
    """
    Ejecuta un benchmark completo: genera respuestas con cada LLM
    disponible para la misma pregunta y las evalúa con el juez.

    Body:
        pregunta      (str):  consulta a evaluar
        session_id    (str):  sesión de referencia
        judge_provider (str): LLM que actúa como juez

    Retorna: respuestas de cada modelo + evaluación del juez + ranking
    """
    from app.agents.agent_judge    import JudgeAgent
    from app.agents.orchestrator   import MedicalOrchestratorAgent
    from app.core.llm_factory      import get_llm, NoLLM

    pregunta       = payload.get("pregunta", "").strip()
    session_id     = payload.get("session_id", "benchmark")
    judge_provider = payload.get("judge_provider", "openai")

    if not pregunta:
        raise HTTPException(status_code=400, detail="Se requiere 'pregunta'")

    proveedores = ["openai", "claude", "ollama"]
    respuestas  = {}
    contextos   = {}
    errores     = {}

    for proveedor in proveedores:
        try:
            llm = get_llm(proveedor)
            if isinstance(llm, NoLLM):
                errores[proveedor] = "No disponible (sin API key)"
                continue

            orch   = MedicalOrchestratorAgent(db=db, llm_provider=proveedor)
            result = orch.handle(message=pregunta, session_id=f"{session_id}_{proveedor}")
            respuestas[proveedor] = result.get("response", "")
            contextos[proveedor]  = result.get("pipeline", {})
        except Exception as e:
            errores[proveedor] = str(e)

    if not respuestas:
        return {"error": "Ningún modelo disponible", "errores": errores}

    # Contexto unificado para el juez (usar el del primer modelo disponible)
    contexto_ref = contextos.get(list(respuestas.keys())[0], {})

    judge   = JudgeAgent(judge_provider=judge_provider)
    ranking = judge.compare_models(
        pregunta=pregunta,
        contexto=str(contexto_ref),
        respuestas=respuestas,
    )

    return {
        "pregunta":    pregunta,
        "benchmark":   ranking,
        "respuestas":  respuestas,
        "errores":     errores,
        "judge":       judge_provider,
        "modelos_probados": list(respuestas.keys()),
    }



# ── Métricas RAG avanzadas ────────────────────────────────────────────
@router.post("/rag/metrics")
async def rag_metrics_evaluate(payload: dict, db: Session = Depends(get_db)) -> dict:
    """
    Calcula métricas RAG avanzadas: Precision@K, Recall@K, NDCG@K, MRR.

    Body:
        relevances     (list[int]): [1,0,1,1,0] — 1=relevante, 0=no relevante
                                    en el orden de ranking retornado por el RAG
        k              (int):       top-K a evaluar (default 5)
        total_relevant (int):       total docs relevantes en corpus (opcional)
        rrf_scores     (list[float]): scores RRF del RAG (alternativa a relevances)
        umbral_rrf     (float):     umbral RRF para considerar relevante (default 0.01)

    Retorna: precision_at_k, recall_at_k, ndcg_at_k, mrr, average_precision
    """
    from app.core.ndcg_metrics import RAGMetrics
    metrics = RAGMetrics()

    k              = int(payload.get("k", 5))
    total_relevant = payload.get("total_relevant", None)
    rrf_scores     = payload.get("rrf_scores", [])
    umbral         = float(payload.get("umbral_rrf", 0.01))
    relevances     = payload.get("relevances", [])

    # Si vienen RRF scores, convertir a binario
    if rrf_scores and not relevances:
        relevances = metrics.relevances_from_rrf(rrf_scores, umbral=umbral)

    if not relevances:
        raise HTTPException(status_code=400,
            detail="Se requiere 'relevances' o 'rrf_scores'")

    result = metrics.compute_all(relevances, k=k, total_relevant=total_relevant)
    result["relevances_usadas"] = relevances
    result["k"] = k
    return result


@router.get("/rag/metrics/search")
async def rag_search_with_metrics(
    q: str = Query(..., description="Consulta de búsqueda"),
    db: Session = Depends(get_db)
) -> dict:
    """
    Ejecuta búsqueda RAG y retorna resultados + métricas automáticas.
    Las métricas se calculan usando el umbral RRF (score >= 0.01 = relevante).
    """
    from app.core.ndcg_metrics import RAGMetrics
    from app.tools.consultas_tool import ConsultasTool

    if not rag_engine.is_built:
        raise HTTPException(status_code=503,
            detail="Índice RAG no construido. Usa POST /api/rag/index primero.")

    resultados = rag_engine.search(q)
    rrf_scores = [r.get("score", 0) for r in resultados]

    metrics_calc = RAGMetrics()
    relevances   = metrics_calc.relevances_from_rrf(rrf_scores, umbral=0.01)
    rag_metrics  = metrics_calc.compute_all(relevances, k=min(5, len(relevances)))

    return {
        "query":      q,
        "resultados": resultados,
        "metricas":   rag_metrics,
        "rrf_scores": rrf_scores,
        "relevances": relevances,
        "resumen":    metrics_calc.summary(rag_metrics),
    }

# ── Historia Clínica — extracción desde texto o audio ─────────────────────
@router.post("/hc/extract")
async def extract_hc(payload: dict, db: Session = Depends(get_db)) -> dict:
    """
    Extrae los campos de Historia Clínica desde texto o audio.
    Resuelve IDs desde tablas catálogo MySQL automáticamente:
      - idMedicamentos      ← busca en tabla medicamentos
      - viasAdministracion_id ← busca en hc_vias_administracion
      - unidad_id           ← busca en hc_unidadmedicamento
      - pauta_id            ← busca en receta_pauta
      - id_examen           ← busca en hc_tipos_examenes
      - tipoRevision_id     ← busca en tipo_revision_organos_sistemas
      - tipoExamen_id       ← busca en tipo_examen_fisico

    Retorna:
      hc     → JSON con IDs resueltos (listo para el frontend)
      hc_raw → JSON con nombres textuales (referencia)
    """
    from app.agents.agent_hc_extractor import HCExtractorAgent, segmentar_conversacion
    from app.core.config import settings

    texto    = payload.get("texto", "").strip()
    es_audio = bool(payload.get("es_audio", False))

    if not texto:
        return {"ok": False, "error": "texto vacío",
                "hc": _hc_fallback("", {}, "texto_vacio")}

    try:
        # Pasamos la DB para resolución de IDs desde catálogo
        agente    = HCExtractorAgent(
            llm_provider=getattr(settings, "llm_provider", None),
            db=db
        )
        resultado = agente.extract(texto, es_audio=es_audio)
        return resultado
    except Exception as e:
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
