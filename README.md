# 🏥 Medical RAG Agent — San Marcos Guayaquil

> Sistema multi-agente con Recuperación Aumentada de Información (RAG) para soporte clínico.  
> Permite al personal médico consultar la base de datos institucional mediante lenguaje natural (voz o texto) y genera automáticamente los campos de la Historia Clínica.

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.116-009688?style=flat&logo=fastapi&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-8.0-4479A1?style=flat&logo=mysql&logoColor=white)
![Langfuse](https://img.shields.io/badge/Langfuse-v4-7C3AED?style=flat)
![LLM](https://img.shields.io/badge/LLM-GPT--4o--mini%20%7C%20Claude%20%7C%20Ollama-FF6B35?style=flat)

---

## 📋 Tabla de Contenidos

- [Descripción](#-descripción)
- [Arquitectura](#-arquitectura)
- [Agentes especializados](#-agentes-especializados)
- [Requisitos](#-requisitos)
- [Instalación](#-instalación)
- [Configuración](#-configuración)
- [Uso](#-uso)
- [Endpoints API](#-endpoints-api)
- [Estructura del proyecto](#-estructura-del-proyecto)
- [Métricas y observabilidad](#-métricas-y-observabilidad)
- [Historia Clínica — extracción automática](#-historia-clínica--extracción-automática)
- [LLM-as-a-Judge](#-llm-as-a-judge)
- [Pruebas](#-pruebas)

---

## 📖 Descripción

El sistema permite al personal médico de la Clínica San Marcos Guayaquil:

- **Consultar** el historial clínico de pacientes por nombre, ID o cédula
- **Buscar** casos similares semánticamente (síntomas, diagnósticos, enfermedades)
- **Obtener** estadísticas globales del sistema clínico
- **Generar** automáticamente los campos de la Historia Clínica desde conversaciones de audio o chat, con resolución de IDs desde las tablas catálogo de MySQL
- **Comparar** la calidad de respuestas entre modelos LLM (GPT-4o-mini vs Claude vs Ollama)

Todo mediante lenguaje natural en español, con soporte de voz (Web Speech API) y métricas de observabilidad completas en Langfuse.

---

## 🏗 Arquitectura

```
Usuario (voz/texto)
       ↓
   AudioAgent                 ← Agente 3: STT + clasificación de intent
       ↓ intent + keywords
   Orquestador                ← Router de rutas fijas deterministas
       ↓              ↓              ↓
DataScienceAgent   PatientAgent   RAGAgent
(estadisticas)     (buscar_pac.)  (consulta_medica / general)
       ↓              ↓              ↓
              ComposerAgent          ← Síntesis final con LLM
                    ↓
             Respuesta al usuario
                    ↓
            HCExtractorAgent         ← Extrae HC → JSON con IDs de catálogo
                    ↓
               JudgeAgent            ← LLM-as-a-Judge: evalúa calidad
                    ↓
               Langfuse              ← 18 scores de usabilidad
```

### Rutas fijas del orquestador

| Intent | Agentes invocados | Herramientas |
|--------|------------------|--------------|
| `estadisticas` | DataScienceAgent | `sql_summary` |
| `buscar_paciente` | PatientAgent | `sql_pacientes` + `sql_historial` |
| `consulta_medica` | RAGAgent + PatientAgent | `rag_search` + `sql_consultas` |
| `general` | RAGAgent | `rag_search` |

---

## 🤖 Agentes especializados

| Agente | Archivo | Responsabilidad única |
|--------|---------|----------------------|
| `AudioAgent` | `audio_agent.py` | STT + normalización + clasificación de intent |
| `DataScienceAgent` | `agent_data_science.py` | Solo estadísticas globales |
| `PatientAgent` | `agent_patient.py` | Solo búsqueda de pacientes (ID/cédula/nombre) |
| `RAGAgent` | `agent_rag.py` | Solo búsqueda semántica TF-IDF + BM25 + RRF |
| `ComposerAgent` | `agent_composer.py` | Solo síntesis de respuesta con LLM |
| `HCExtractorAgent` | `agent_hc_extractor.py` | Extracción HC + resolución IDs catálogo |
| `JudgeAgent` | `agent_judge.py` | Evaluación LLM-as-a-Judge (5 criterios) |

---

## ⚙️ Requisitos

- Python **3.11+**
- MySQL **8.0** (base de datos `sanmarcosguayaquil`)
- API key de **OpenAI** y/o **Anthropic** (o Ollama local)
- Cuenta en **Langfuse** (opcional, para observabilidad)

---

## 🚀 Instalación

### 1. Clonar el repositorio

```bash
git clone https://github.com/juandiriver-stack/medical-rag-llm-project.git
cd medical-rag-llm-project
```

### 2. Crear y activar entorno virtual

```bash
# Windows
python -m venv venv
.\venv\Scripts\Activate.ps1

# Linux / Mac
python -m venv venv
source venv/bin/activate
```

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 4. Instalar Langfuse (observabilidad)

```bash
pip install langfuse
```

### 5. Configurar variables de entorno

```bash
cp .env.example .env
# Editar .env con tus credenciales (ver sección Configuración)
```

### 6. Iniciar el servidor

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 7. Construir el índice RAG

Abre el navegador en `http://localhost:8000` y haz clic en **"Construir índice"**, o ejecuta:

```bash
curl -X POST http://localhost:8000/api/rag/index
```

---

## 🔧 Configuración

Edita el archivo `.env` con tus credenciales:

```env
# ── Base de datos MySQL ───────────────────────────────────────────────
DB_HOST=181.39.74.126
DB_PORT=3306
DB_NAME=sanmarcosguayaquil
DB_USER=tu_usuario
DB_PASSWORD=tu_contraseña

# ── Proveedor LLM: claude | openai | ollama ───────────────────────────
LLM_PROVIDER=openai

# OpenAI / ChatGPT
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# Anthropic / Claude
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-sonnet-4-20250514

# Ollama (local, sin internet)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1

# ── Langfuse observabilidad (opcional) ───────────────────────────────
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

> **Nota:** Si no configuras Langfuse, el sistema entra automáticamente en modo NOOP y funciona normalmente sin observabilidad.

---

## 💬 Uso

### Interfaz web

Abre `http://localhost:8000` en tu navegador. El sistema soporta:

- ⌨️ **Texto**: escribe tu consulta en el chat
- 🎤 **Voz**: usa el micrófono (Web Speech API, locale `es-ES`)
- ⬇️ **HC JSON**: cada respuesta incluye el botón de descarga del JSON de Historia Clínica

### Ejemplos de consultas

```
# Estadísticas globales
"¿Cuáles son las enfermedades más frecuentes?"
"Resumen estadístico del sistema"

# Buscar paciente
"Historial de GRECIA MARIBEL MURIEL CALERO"
"Consultas del paciente ID 39121"
"Cédula 0921097408"

# Consulta clínica
"Soy el Dr. García, el paciente tiene fiebre y dolor de cabeza"
"Pacientes con lumbalgia crónica"
"¿Hay casos de diabetes en la base de datos?"

# Historia Clínica desde audio/chat
"Doctor: Le receto Ciprofloxacino 500mg vía oral cada 12 horas durante 10 días.
Paciente: Tengo ardor al orinar desde hace 3 días y fiebre."
```

### Acciones rápidas (sidebar)

El panel lateral incluye 5 casos de prueba piloto predefinidos con el formato estándar para pruebas con médicos:

- **Caso 1** — Ginecología: dolor pélvico + fiebre
- **Caso 2** — Cardiología: dolor torácico opresivo
- **Caso 3** — Pediatría: fiebre + otalgia en niño
- **Caso 4** — Traumatología: lumbalgia crónica
- **Caso 5** — Neurología: cefalea pulsátil hemicraneal

---

## 🔌 Endpoints API

### Chat y consultas
| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/api/chat` | Pipeline principal: procesa consulta y retorna respuesta |
| `GET` | `/api/summary` | Estadísticas globales del sistema |
| `GET` | `/api/health` | Estado del servidor y conexiones |

### RAG
| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/api/rag/index` | Construye índice RAG en RAM desde MySQL |
| `GET` | `/api/rag/metrics/search?q=` | Búsqueda RAG + Precision@K, NDCG@K, MRR |
| `POST` | `/api/rag/metrics` | Calcula métricas desde `relevances` o `rrf_scores` |

### Historia Clínica
| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/api/hc/extract` | Extrae HC desde texto + resuelve IDs de catálogo |
| `POST` | `/api/hc/extract/audio` | Transcribe audio + extrae HC |

### Evaluación LLM-as-a-Judge
| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `POST` | `/api/judge/evaluate` | Evalúa 1 respuesta con 5 criterios (escala 1-5) |
| `POST` | `/api/judge/compare` | Compara respuestas de múltiples modelos |
| `POST` | `/api/judge/benchmark` | Genera + evalúa respuestas de todos los modelos disponibles |

### Pacientes
| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `GET` | `/api/pacientes` | Lista pacientes |
| `GET` | `/api/pacientes/{id}` | Datos de un paciente |
| `GET` | `/api/pacientes/{id}/consultas` | Historial de un paciente |

> Documentación interactiva: `http://localhost:8000/docs` (Swagger UI)

---

## 📁 Estructura del proyecto

```
medical_rag_project/
├── app/
│   ├── agents/
│   │   ├── audio_agent.py           # Agente 3: STT + intent
│   │   ├── agent_data_science.py    # Estadísticas globales
│   │   ├── agent_patient.py         # Búsqueda de pacientes
│   │   ├── agent_rag.py             # Búsqueda semántica + métricas RAG
│   │   ├── agent_composer.py        # Síntesis con LLM
│   │   ├── agent_hc_extractor.py    # Extracción HC + CatalogResolver
│   │   ├── agent_judge.py           # LLM-as-a-Judge
│   │   └── orchestrator.py          # Pipeline + rutas fijas
│   │
│   ├── core/
│   │   ├── config.py                # Variables de entorno (.env)
│   │   ├── llm_factory.py           # Abstracción Claude/GPT/Ollama
│   │   ├── observability.py         # Langfuse v4 con 18 scores
│   │   ├── metrics.py               # UsabilityMetrics acumulador
│   │   └── ndcg_metrics.py          # Precision@K, Recall@K, NDCG@K, MRR
│   │
│   ├── rag/
│   │   └── engine.py                # TFIDFIndex + BM25Index + RRF
│   │
│   ├── tools/
│   │   └── consultas_tool.py        # SQL directo a MySQL
│   │
│   ├── prompts/                     # Instrucciones de cada agente
│   │   ├── agent_audio.md           # Clasificación de intent (20+ ejemplos)
│   │   ├── agent_composer.md        # Síntesis médica
│   │   ├── agent_rag.md             # Búsqueda semántica (14 especialidades)
│   │   ├── agent_patient.md         # Búsqueda de pacientes
│   │   ├── agent_data_science.md    # Estadísticas
│   │   ├── agent_sql.md             # Consultas SQL
│   │   ├── agent_tools.md           # Selector de herramientas
│   │   ├── agent_judge.md           # Criterios de evaluación
│   │   └── agent_hc_extractor.md    # Extracción HC + tablas catálogo
│   │
│   ├── api/
│   │   ├── routes.py                # 14+ endpoints FastAPI
│   │   └── schemas.py               # Modelos Pydantic
│   │
│   ├── db/
│   │   ├── models.py                # Modelos SQLAlchemy
│   │   └── session.py               # Conexión MySQL
│   │
│   └── main.py                      # Punto de entrada FastAPI
│
├── static/
│   └── index.html                   # SPA: chat + micrófono + descarga HC
│
├── .env.example                     # Variables de entorno de ejemplo
├── requirements.txt                 # Dependencias Python
├── Dockerfile                       # Imagen Docker
└── docker-compose.yml               # Orquestación de contenedores
```

---

## 📊 Métricas y observabilidad

El sistema registra **18 scores por conversación** en Langfuse:

### Latencia por agente
| Score | Descripción |
|-------|-------------|
| `total_latency_ms` | Tiempo total de la conversación |
| `llm_latency_ms` | Solo el LLM (ComposerAgent) |
| `sql_latency_ms` | Agente SQL más lento |
| `rag_latency_ms` | Búsqueda semántica |
| `audio_latency_ms` | AudioAgent (clasificación) |

### Métricas RAG
| Score | Descripción |
|-------|-------------|
| `chunks_retrieved` | Chunks obtenidos por búsqueda |
| `chunks_used_est` | Chunks con RRF score ≥ 0.01 |
| `chunks_ratio` | chunks_used / chunks_retrieved |
| `top_rrf_score` | Score del chunk más relevante |
| `avg_rrf_score` | Score promedio de todos los chunks |

### Distribución de errores
| Score | Descripción |
|-------|-------------|
| `error` | 1.0 si hubo cualquier error |
| `error_llm` | LLM no disponible |
| `error_sql` | Fallo en consulta SQL |
| `error_rag` | Índice RAG no construido |

### Volumen y calidad
| Score | Descripción |
|-------|-------------|
| `sql_queries_executed` | Número de queries SQL |
| `rag_used` | 1.0 si se usó RAG |
| `intent_code` | 1=estadisticas, 2=buscar_paciente, 3=consulta_medica, 4=general |
| `judge_score_promedio` | Score LLM-as-a-Judge (1-5) |
| `judge_alertas` | Criterios bajo umbral mínimo |

### Métricas RAG avanzadas (endpoint `/api/rag/metrics/search`)

```json
{
  "precision_at_5": 0.60,
  "recall_at_5":    1.00,
  "ndcg_at_5":      0.906,
  "mrr":            1.00,
  "average_precision": 0.806
}
```

---

## 📋 Historia Clínica — extracción automática

El sistema extrae automáticamente los campos de la HC desde conversaciones y resuelve los IDs desde las tablas catálogo de MySQL:

### Tablas catálogo consultadas

| Sección HC | Tabla catálogo | Campo | ID resultante |
|-----------|---------------|-------|---------------|
| Recetas | `medicamentos` | `nombre` | `idMedicamentos` |
| Recetas | `hc_vias_administracion` | `nombre` | `viasAdministracion_id` |
| Recetas | `hc_unidadmedicamento` | `nombre` | `unidad_id` |
| Recetas | `receta_pauta` | `intervalo/frecuencia/durante/nombre` | `pauta_id` |
| Exámenes | `hc_tipos_examenes` | `name` | `id_examen` |
| Revisión órganos | `tipo_revision_organos_sistemas` | `Nombre` | `tipoRevision_id` |
| Examen físico | `tipo_examen_fisico` | `nombre` | `tipoExamen_id` |

### Ejemplo de uso

```bash
curl -X POST http://localhost:8000/api/hc/extract \
  -H "Content-Type: application/json" \
  -d '{
    "texto": "Doctor: Le receto Ciprofloxacino 500mg vía oral cada 12 horas durante 10 días. Paciente: Tengo ardor al orinar desde hace 3 días.",
    "es_audio": false
  }'
```

**Respuesta:**
```json
{
  "ok": true,
  "hc": {
    "motivo_consulta": {
      "motivoConsulta": "ardor al orinar desde hace 3 días",
      "enfermedadActual": "ardor al orinar desde hace 3 días"
    },
    "recetas": [{
      "idMedicamentos": 83,
      "viasAdministracion_id": 3,
      "dosis": 500,
      "unidad_id": 2,
      "pauta_id": 14,
      "dias": 10,
      "total": 20,
      "_nombre_medicamento": "CIPROFLOXACINO"
    }]
  },
  "hc_raw": { "...nombres textuales del LLM..." }
}
```

---

## ⚖️ LLM-as-a-Judge

Evalúa automáticamente la calidad de cada respuesta con 5 criterios (escala 1-5):

| Criterio | Umbral mínimo | Descripción |
|----------|--------------|-------------|
| `relevancia` | ≥ 3.0 | ¿La respuesta responde la pregunta? |
| `fidelidad` | ≥ 4.0 | ¿Solo usa datos reales sin inventar? |
| `completitud` | ≥ 3.0 | ¿Incluye toda la información disponible? |
| `claridad` | ≥ 3.0 | ¿Es clara y profesional? |
| `seguridad_clinica` | ≥ 4.0 | ¿Evita recomendaciones peligrosas? |

### Benchmark de modelos

```bash
curl -X POST http://localhost:8000/api/judge/benchmark \
  -H "Content-Type: application/json" \
  -d '{
    "pregunta": "Soy el Dr. García, el paciente tiene fiebre y dolor lumbar",
    "judge_provider": "openai"
  }'
```

---

## 🧪 Pruebas

### Ejecutar validaciones unitarias

```bash
cd medical_rag_project
python -m pytest tests/ -v
```

### Prueba rápida del pipeline

```bash
# Verificar que el servidor está corriendo
curl http://localhost:8000/api/health

# Construir índice RAG
curl -X POST http://localhost:8000/api/rag/index

# Enviar consulta de prueba
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "¿Cuáles son las enfermedades más frecuentes?", "session_id": "test"}'
```

### Validar métricas RAG

```bash
curl "http://localhost:8000/api/rag/metrics/search?q=dolor+de+cabeza"
```

---

## 🐳 Docker

```bash
# Construir imagen
docker build -t medical-rag-agent .

# Ejecutar con docker-compose
docker-compose up -d
```

---

## 📚 Referencias

- Lewis et al. (2020). *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks*
- Zheng et al. (2023). *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena*
- Robertson & Zaragoza (2009). *BM25 and Beyond*
- Järvelin & Kekäläinen (2002). *Normalized Discounted Cumulative Gain*
- Manning, Raghavan & Schütze (2008). *Introduction to Information Retrieval*

---

## 👥 Autores

- **Juan Diego Riofrio Maila**
- **Oscar Santiago Figueroa Alemán**
- **María Sofía Molina Sandoval**

**Director de tesis:** Ing. César Andrés Ron Cusme  
**Institución:** Clínica San Marcos Guayaquil

---

## 📄 Licencia

Proyecto académico — Trabajo de Titulación  
Todos los derechos reservados © 2025
