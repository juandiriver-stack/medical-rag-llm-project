from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"🚀 {settings.app_name} iniciado — proveedor LLM: {settings.llm_provider}")
    print(f"📦 Base de datos: {settings.db_host}:{settings.db_port}/{settings.db_name}")
    yield
    print("⛔ Apagando servidor...")


app = FastAPI(
    title=settings.app_name,
    description="Sistema RAG médico con soporte para Claude, ChatGPT y Ollama",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix=settings.api_prefix)

# Sirve el frontend estático
try:
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
except Exception:
    pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=settings.debug)
