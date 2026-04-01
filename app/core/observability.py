"""
Observabilidad con Langfuse — trazas y spans para cada agente/tool.

Variables de entorno requeridas en .env:
  LANGFUSE_PUBLIC_KEY=pk-lf-...
  LANGFUSE_SECRET_KEY=sk-lf-...
  LANGFUSE_HOST=https://cloud.langfuse.com   (o self-hosted)

Si las variables no están configuradas, el módulo opera en modo NOOP:
todos los métodos funcionan pero no envían nada a Langfuse.
"""
from __future__ import annotations
import os
import time
from contextlib import contextmanager
from typing import Any

from app.core.config import settings


class _NoopSpan:
    """Span nulo — usado cuando Langfuse no está configurado."""
    def update(self, **_): pass
    def end(self, **_): pass
    def __enter__(self): return self
    def __exit__(self, *_): pass


class _NoopTrace:
    def span(self, **_): return _NoopSpan()
    def update(self, **_): pass
    def __enter__(self): return self
    def __exit__(self, *_): pass


class LangfuseObserver:
    """
    Wrapper ligero sobre el SDK de Langfuse.
    Crea trazas por conversación y spans por cada agente/tool.
    Manejo de errores silencioso: nunca interrumpe el flujo principal.
    """

    def __init__(self) -> None:
        self._client = None
        self._enabled = False
        self._init()

    def _init(self) -> None:
        pk   = getattr(settings, "langfuse_public_key", "") or os.getenv("LANGFUSE_PUBLIC_KEY", "")
        sk   = getattr(settings, "langfuse_secret_key", "") or os.getenv("LANGFUSE_SECRET_KEY", "")
        host = getattr(settings, "langfuse_host", "")       or os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

        if not (pk and sk and len(pk) > 5 and len(sk) > 5):
            print("INFO  Langfuse: sin credenciales — modo NOOP activo")
            return
        try:
            from langfuse import Langfuse
            self._client  = Langfuse(public_key=pk, secret_key=sk, host=host)
            self._enabled = True
            print(f"INFO  Langfuse conectado → {host}")
        except Exception as e:
            print(f"WARN  Langfuse init error: {e} — modo NOOP")

    # ── API pública ──────────────────────────────────────────────────────

    def start_trace(self, name: str, session_id: str, user_input: str, metadata: dict | None = None):
        """Inicia una traza por conversación. Retorna objeto trace o noop."""
        if not self._enabled:
            return _NoopTrace()
        try:
            return self._client.trace(
                name=name,
                session_id=session_id,
                input=user_input,
                metadata=metadata or {},
            )
        except Exception as e:
            print(f"WARN  Langfuse start_trace: {e}")
            return _NoopTrace()

    def span(self, trace, name: str, input_data: Any = None, metadata: dict | None = None):
        """Crea un span dentro de la traza."""
        if not self._enabled or isinstance(trace, _NoopTrace):
            return _NoopSpan()
        try:
            return trace.span(
                name=name,
                input=input_data,
                metadata=metadata or {},
                start_time=time.time(),
            )
        except Exception as e:
            print(f"WARN  Langfuse span: {e}")
            return _NoopSpan()

    def end_span(self, span, output: Any = None, tokens: dict | None = None) -> None:
        """Cierra un span con su output y tokens usados."""
        try:
            kwargs: dict = {"output": output}
            if tokens:
                kwargs["usage"] = tokens
            span.end(**kwargs)
        except Exception as e:
            print(f"WARN  Langfuse end_span: {e}")

    def end_trace(self, trace, output: Any = None) -> None:
        try:
            trace.update(output=output)
        except Exception:
            pass

    def flush(self) -> None:
        """Envía eventos pendientes (llamar al cerrar la app)."""
        if self._enabled:
            try:
                self._client.flush()
            except Exception:
                pass

    @property
    def enabled(self) -> bool:
        return self._enabled


# Instancia global compartida
observer = LangfuseObserver()
