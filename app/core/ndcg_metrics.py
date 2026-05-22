"""
Métricas RAG avanzadas: Precision@K, Recall@K, NDCG@K, MRR

Implementadas según solicitud del tutor para evaluación académica del
motor de búsqueda semántica híbrida (TF-IDF + BM25 + RRF).

Uso:
    from app.core.ndcg_metrics import RAGMetrics
    metrics = RAGMetrics()

    # Con ground truth binario (relevante=1, no relevante=0)
    retrieved = [1, 0, 1, 1, 0]   # orden de los chunks recuperados
    scores = metrics.compute_all(retrieved, k=5)
    print(scores)
    # {'precision_k': 0.6, 'recall_k': 1.0, 'ndcg_k': 0.848, 'mrr': 1.0}

    # Integración con Langfuse
    metrics.register_langfuse(span, retrieved, k=5)
"""
from __future__ import annotations
import math
from typing import Any


class RAGMetrics:
    """
    Calcula métricas estándar de recuperación de información
    para evaluar el motor RAG híbrido.
    """

    # ── Precision@K ───────────────────────────────────────────────────
    @staticmethod
    def precision_at_k(relevances: list[int], k: int) -> float:
        """
        Fracción de los K documentos recuperados que son relevantes.
        Precision@K = |{relevantes en top-K}| / K

        Args:
            relevances: lista binaria [1,0,1,...] en orden de ranking
            k:          número de documentos a considerar
        """
        if k <= 0 or not relevances:
            return 0.0
        top_k = relevances[:k]
        return round(sum(top_k) / k, 4)

    # ── Recall@K ──────────────────────────────────────────────────────
    @staticmethod
    def recall_at_k(relevances: list[int], k: int,
                    total_relevant: int | None = None) -> float:
        """
        Fracción de documentos relevantes recuperados en top-K.
        Recall@K = |{relevantes en top-K}| / total_relevantes

        Args:
            relevances:     lista binaria [1,0,1,...] en orden de ranking
            k:              número de documentos a considerar
            total_relevant: total de documentos relevantes (si None, usa sum(relevances))
        """
        if not relevances:
            return 0.0
        total = total_relevant or sum(relevances)
        if total == 0:
            return 0.0
        retrieved_relevant = sum(relevances[:k])
        return round(retrieved_relevant / total, 4)

    # ── NDCG@K ────────────────────────────────────────────────────────
    @staticmethod
    def ndcg_at_k(relevances: list[int | float], k: int) -> float:
        """
        Normalized Discounted Cumulative Gain @K.
        Mide la calidad del ranking: penaliza documentos relevantes
        que aparecen en posiciones bajas.

        NDCG@K = DCG@K / IDCG@K
        DCG@K  = Σ (2^rel_i - 1) / log2(i + 1)   para i=1..K
        IDCG@K = DCG del ranking perfecto (todos relevantes primero)

        Args:
            relevances: lista de relevancias (binaria o graduada) en orden de ranking
            k:          número de documentos a considerar
        """
        if not relevances or k <= 0:
            return 0.0

        def dcg(rels: list, k: int) -> float:
            return sum(
                (2 ** r - 1) / math.log2(i + 2)
                for i, r in enumerate(rels[:k])
            )

        actual_dcg = dcg(relevances, k)
        # Ranking ideal: documentos más relevantes primero
        ideal_dcg  = dcg(sorted(relevances, reverse=True), k)

        if ideal_dcg == 0:
            return 0.0
        return round(actual_dcg / ideal_dcg, 4)

    # ── MRR ───────────────────────────────────────────────────────────
    @staticmethod
    def mean_reciprocal_rank(relevances: list[int]) -> float:
        """
        Mean Reciprocal Rank: posición inversa del primer documento relevante.
        MRR = 1 / posición_del_primer_relevante

        Útil para medir si el documento más relevante aparece primero.
        """
        for i, r in enumerate(relevances, 1):
            if r > 0:
                return round(1.0 / i, 4)
        return 0.0

    # ── Average Precision ─────────────────────────────────────────────
    @staticmethod
    def average_precision(relevances: list[int]) -> float:
        """
        Average Precision: promedio de Precision@K en cada posición
        donde hay un documento relevante.

        AP = Σ P@i * rel_i / total_relevantes
        """
        total_relevant = sum(relevances)
        if total_relevant == 0:
            return 0.0
        ap = 0.0
        running_relevant = 0
        for i, r in enumerate(relevances, 1):
            if r > 0:
                running_relevant += 1
                ap += running_relevant / i
        return round(ap / total_relevant, 4)

    # ── Compute all ───────────────────────────────────────────────────
    def compute_all(self, relevances: list[int], k: int = 5,
                    total_relevant: int | None = None) -> dict[str, float]:
        """
        Calcula todas las métricas de una vez.

        Args:
            relevances:     [1,0,1,1,0] — orden de ranking, 1=relevante
            k:              top-K a evaluar (default 5, igual que el RAGAgent)
            total_relevant: total documentos relevantes en el corpus

        Returns:
            dict con todas las métricas
        """
        return {
            f"precision_at_{k}":  self.precision_at_k(relevances, k),
            f"recall_at_{k}":     self.recall_at_k(relevances, k, total_relevant),
            f"ndcg_at_{k}":       self.ndcg_at_k(relevances, k),
            "mrr":                self.mean_reciprocal_rank(relevances),
            "average_precision":  self.average_precision(relevances),
        }

    # ── Integración con Langfuse ──────────────────────────────────────
    def register_langfuse(self, span: Any, relevances: list[int],
                          k: int = 5,
                          total_relevant: int | None = None) -> None:
        """
        Registra todas las métricas RAG como scores en Langfuse.
        Llama después de que el médico evalúe la relevancia de los chunks.
        """
        if span is None:
            return
        metrics = self.compute_all(relevances, k, total_relevant)
        for name, value in metrics.items():
            try:
                span.score_trace(
                    name=f"rag_{name}",
                    value=float(value),
                    comment=f"Métrica RAG {name} = {value}"
                )
            except Exception:
                pass

    # ── Evaluación desde RRF scores ───────────────────────────────────
    @staticmethod
    def relevances_from_rrf(rrf_scores: list[float],
                            umbral: float = 0.01) -> list[int]:
        """
        Convierte scores RRF en lista binaria de relevancia.
        Un chunk se considera relevante si su RRF score >= umbral.

        Args:
            rrf_scores: scores de similitud del RAGAgent
            umbral:     mínimo score para considerar relevante (default 0.01)
        """
        return [1 if s >= umbral else 0 for s in rrf_scores]

    # ── Resumen legible ───────────────────────────────────────────────
    @staticmethod
    def summary(metrics: dict) -> str:
        """Formatea las métricas para log de consola."""
        lines = ["Métricas RAG:"]
        for k, v in metrics.items():
            lines.append(f"  {k:25s} = {v:.4f}")
        return "\n".join(lines)
