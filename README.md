# 🏥 Medical RAG Agent — San Marcos Guayaquil

Sistema de agente médico multi-capa con **RAG**, **3 agentes especializados**, **STT/TTS** y soporte para **Claude**, **ChatGPT** y **Ollama**.

---

## Arquitectura — 3 agentes

```
Usuario (voz / texto)
    │
    ▼
[Agente 3 — AudioAgent]          app/agents/audio_agent.py
    • STT: browser / Whisper / Google
    • Normalización de texto
    • Extracción de intención con LLM
    • Persistencia en conversation_sessions
    │
    ▼
[Agente 2 — ToolAgent]           app/agents/tool_agent.py
    • Selección dinámica de herramientas
    • sql_consultas → tabla consultas (motivoConsulta, enfermedadActual, examenFisico)
    • sql_pacientes → tabla pacientes
    • rag_search    → TF-IDF semántico
    • sql_summary   → estadísticas
    • sql_historial → historial por paciente
    │
    ▼
[Agente 1 — Orquestador]         app/agents/orchestrator.py
    • Síntesis final con LLM seleccionado
    • TTS opcional (browser / OpenAI / Google)
    • Log en conversation_logs
    │
    ▼
Respuesta al usuario (texto + audio opcional)
```

---

## Instalación rápida

```bash
# 1. Extraer ZIP y abrir en VS Code
unzip medical_rag_project.zip
code medical_rag_project

# 2. Crear y activar entorno virtual
python -m venv venv
source venv/bin/activate          # Mac/Linux
.\venv\Scripts\Activate.ps1       # Windows

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar .env
cp .env.example .env
# Editar con IP pública MySQL + API keys

# 5. Iniciar servidor
uvicorn app.main:app --reload --port 8000

# 6. Construir índice RAG
curl -X POST http://localhost:8000/api/rag/index
```

---

## Variables de entorno clave

| Variable | Descripción |
|---|---|
| `DB_HOST` | IP pública del servidor MySQL |
| `DB_NAME` | `sanmarcosguayaquil` |
| `LLM_PROVIDER` | `claude` / `openai` / `ollama` |
| `ANTHROPIC_API_KEY` | Clave API de Anthropic |
| `OPENAI_API_KEY` | Clave API de OpenAI |
| `STT_PROVIDER` | `browser` / `whisper` / `google` |
| `TTS_PROVIDER` | `browser` / `openai` / `google` |
| `ENABLE_TTS` | `true` / `false` |

---

## Endpoints principales

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/health` | Estado + índice RAG |
| `POST` | `/api/rag/index` | Construir índice RAG |
| `GET` | `/api/rag/search?q=` | Búsqueda semántica |
| `POST` | `/api/chat` | Chat texto → 3 agentes |
| `POST` | `/api/chat/audio` | Audio → STT → 3 agentes |
| `GET` | `/api/session/{id}/history` | Historial de sesión |
| `POST` | `/api/tts` | TTS standalone |
| `GET` | `/api/consultas` | Listar consultas |
| `GET` | `/api/pacientes` | Listar pacientes |
| `GET` | `/api/summary` | Estadísticas |
| `GET` | `/docs` | Swagger UI |

---

## Payload chat

```json
POST /api/chat
{
  "message": "¿Cuáles son los motivos de consulta más frecuentes?",
  "provider": "claude",
  "session_id": "paciente_001",
  "tts": true
}
```

Respuesta:
```json
{
  "source": "orchestrator",
  "intent": "consulta_medica",
  "provider": "claude",
  "response": "Basándome en los registros...",
  "tools_used": ["rag_search", "sql_consultas"],
  "rag_used": true,
  "session_id": "paciente_001",
  "pipeline": {
    "agent3_intent": "consulta_medica",
    "agent3_keywords": ["motivos", "consulta"],
    "agent2_tools": ["rag_search", "sql_consultas"]
  },
  "tts": { "mode": "browser", "text": "...", "voice": "es-EC" }
}
```

---

## STT — Configuración por proveedor

### Browser (por defecto, sin costo)
```
STT_PROVIDER=browser
```
Usa la Web Speech API del navegador. Solo Chrome/Edge. Sin costo.

### Whisper (local, sin internet)
```bash
pip install openai-whisper
STT_PROVIDER=whisper
WHISPER_MODEL=base   # tiny | base | small | medium
```

### Google Cloud Speech
```bash
pip install google-cloud-speech
export GOOGLE_APPLICATION_CREDENTIALS=/ruta/credenciales.json
STT_PROVIDER=google
```

---

## TTS — Configuración por proveedor

### Browser (por defecto)
```
TTS_PROVIDER=browser
TTS_VOICE=es-EC
```

### OpenAI TTS
```
TTS_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

### Google Cloud TTS
```bash
pip install google-cloud-texttospeech
TTS_PROVIDER=google
```
