"""
Observabilidad con Langfuse v4 — granularidad completa

Qué se registra por cada conversación:
  TRAZA raíz:
    - input: pregunta del usuario
    - output: respuesta final + intent + agentes usados + elapsed_s

  SPANS por agente:
    agent3_audio     → intent, keywords, ruta asignada
    agent_data_science → SQL exacto ejecutado, métricas retornadas
    agent_patient    → SQL exacto, entidad detectada (id/cédula/nombre)
    agent_rag        → chunks RAG con score de similitud y metadata
    agent_composer   → tokens estimados, proveedor LLM

  SCORES en cada span (aparecen en dashboard Langfuse):
    latency_ms       → latencia en milisegundos del agente
    chunks_retrieved → número de chunks RAG obtenidos (en agent_rag)
    chunks_used      → número de chunks efectivamente usados

  SCORES en la traza completa:
    total_latency_ms → latencia total de la conversación
    rag_used         → 1.0 si se usó RAG, 0.0 si no
    error            → 1.0 si hubo error, 0.0 si exitoso
"""
from __future__ import annotations
import os
import time
from typing import Any
from app.core.config import settings


class _NoopCtx:
    """Span nulo — no hace nada, no falla."""
    def end(self, **_):               pass
    def update(self, **_):            pass
    def score(self, **_):             pass
    def score_trace(self, **_):       pass
    def __enter__(self):              return self
    def __exit__(self, *_):           pass


class LangfuseObserver:

    def __init__(self) -> None:
        self._client  = None
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
            client = Langfuse(public_key=pk, secret_key=sk, host=host)
            if client.auth_check():
                self._client  = client
                self._enabled = True
                print(f"INFO  Langfuse v4 conectado → {host}")
            else:
                print("WARN  Langfuse: auth_check falló — modo NOOP")
        except Exception as e:
            print(f"WARN  Langfuse init error: {e} — modo NOOP")

    # ── Traza raíz ────────────────────────────────────────────────────
    def start_trace(self, name: str, session_id: str,
                    user_input: str, metadata: dict | None = None):
        if not self._enabled:
            return _NoopCtx()
        try:
            from langfuse.types import TraceContext
            trace_id = self._client.create_trace_id()
            ctx      = TraceContext(trace_id=trace_id, name=name)
            return self._client.start_observation(
                trace_context=ctx,
                name=name,
                as_type="agent",
                input={"user_input": user_input, "session_id": session_id},
                metadata=metadata or {},
            )
        except Exception as e:
            print(f"WARN  Langfuse start_trace: {e}")
            return _NoopCtx()

    # ── Span hijo ─────────────────────────────────────────────────────
    def span(self, parent, name: str,
             input_data: Any = None, metadata: dict | None = None,
             as_type: str = "span"):
        if not self._enabled or isinstance(parent, _NoopCtx):
            return _NoopCtx()
        try:
            return self._client.start_observation(
                name=name,
                as_type=as_type,
                input=input_data,
                metadata=metadata or {},
            )
        except Exception as e:
            print(f"WARN  Langfuse span '{name}': {e}")
            return _NoopCtx()

    # ── Cerrar span con output + scores opcionales ─────────────────────
    def end_span(self, span, output: Any = None,
                 tokens: dict | None = None,
                 scores: dict | None = None,
                 latency_ms: float | None = None) -> None:
        """
        Cierra un span y opcionalmente registra scores en el dashboard.

        Args:
            span:        el span a cerrar
            output:      datos de salida del agente
            tokens:      {"input": N, "output": N, "total": N}
            scores:      dict arbitrario de scores {nombre: valor_float}
            latency_ms:  latencia en ms — se registra automáticamente como score
        """
        if isinstance(span, _NoopCtx):
            return
        try:
            kwargs: dict = {}
            if output is not None:
                kwargs["output"] = output
            if tokens:
                kwargs["usage_details"] = {
                    "input":  tokens.get("input", 0),
                    "output": tokens.get("output", 0),
                    "total":  tokens.get("total", 0),
                }
            if kwargs:
                span.update(**kwargs)
            span.end()

            # Registrar latencia como score del span
            if latency_ms is not None:
                try:
                    span.score(name="latency_ms", value=round(latency_ms, 2),
                               comment=f"Latencia del agente: {latency_ms:.0f}ms")
                except Exception:
                    pass

            # Registrar scores adicionales del span
            if scores:
                for name, value in scores.items():
                    try:
                        span.score(name=name, value=float(value))
                    except Exception:
                        pass

        except Exception as e:
            print(f"WARN  Langfuse end_span: {e}")

    # ── Score en la traza completa ─────────────────────────────────────
    def score_trace(self, span, name: str, value: float,
                    comment: str = "") -> None:
        """
        Registra un score asociado a la TRAZA completa (no al span).
        Aparece en el dashboard de Langfuse como métrica global.
        """
        if isinstance(span, _NoopCtx) or not self._enabled:
            return
        try:
            span.score_trace(
                name=name,
                value=value,
                comment=comment or f"{name}: {value}",
            )
        except Exception as e:
            print(f"WARN  Langfuse score_trace '{name}': {e}")

    # ── Cerrar traza ──────────────────────────────────────────────────
    def end_trace(self, trace, output: Any = None,
                  scores: dict | None = None) -> None:
        if isinstance(trace, _NoopCtx):
            return
        if not self._enabled or self._client is None:
            return
        try:
            if output is not None:
                trace.update(output=output)

            # Scores finales de la traza completa
            if scores:
                for name, value in scores.items():
                    try:
                        trace.score_trace(
                            name=name, value=float(value),
                            comment=f"Métrica global: {name}={value}"
                        )
                    except Exception:
                        pass

            trace.end()
            self._client.flush()
        except Exception as e:
            print(f"WARN  Langfuse end_trace: {e}")

    def flush(self) -> None:
        if self._enabled and self._client is not None:
            try:
                self._client.flush()
            except Exception:
                pass

    @property
    def enabled(self) -> bool:
        return self._enabled


observer = LangfuseObserver()
