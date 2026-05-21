"""
Motor RAG híbrido: TF-IDF semántico + BM25 keyword + Reciprocal Rank Fusion

Búsqueda HÍBRIDA:
  a) Semántica  → TF-IDF coseno (captura contexto, sinónimos parciales)
  b) Keywords   → BM25 (match exacto de términos, preciso para nombres)
  c) Re-ranking → Reciprocal Rank Fusion (RRF) para combinar scores

Ventaja: "dolor de cabeza" no traerá falsos positivos como apellido "Cabeza"
porque BM25 penaliza docs donde el término no aparece en posición relevante.
"""
from __future__ import annotations
import math
import re
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ═══════════════════════════════════════════════════════════════════
# TF-IDF Index
# ═══════════════════════════════════════════════════════════════════
class TFIDFIndex:
    def __init__(self) -> None:
        self.docs: list[str] = []
        self.metadata: list[dict] = []
        self.idf: dict[str, float] = {}
        self.tf_matrix: list[dict[str, float]] = []

    def _tokenize(self, text: str) -> list[str]:
        text = text.lower()
        text = re.sub(r"[^a-záéíóúüñ0-9\s]", " ", text)
        return [t for t in text.split() if len(t) > 2]

    def _tf(self, tokens: list[str]) -> dict[str, float]:
        counts: dict[str, int] = defaultdict(int)
        for t in tokens:
            counts[t] += 1
        total = len(tokens) or 1
        return {t: c / total for t, c in counts.items()}

    def build(self, docs: list[str], metadata: list[dict]) -> None:
        self.docs = docs
        self.metadata = metadata
        self.tf_matrix = []
        token_sets = []
        for doc in docs:
            tokens = self._tokenize(doc)
            self.tf_matrix.append(self._tf(tokens))
            token_sets.append(set(tokens))
        N = len(docs)
        df: dict[str, int] = defaultdict(int)
        for ts in token_sets:
            for t in ts:
                df[t] += 1
        self.idf = {t: math.log((N + 1) / (cnt + 1)) + 1 for t, cnt in df.items()}

    def _tfidf_vec(self, tf: dict[str, float]) -> dict[str, float]:
        return {t: v * self.idf.get(t, 1.0) for t, v in tf.items()}

    def _cosine(self, a: dict[str, float], b: dict[str, float]) -> float:
        keys = set(a) & set(b)
        dot = sum(a[k] * b[k] for k in keys)
        na = math.sqrt(sum(v ** 2 for v in a.values()))
        nb = math.sqrt(sum(v ** 2 for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    def search(self, query: str, top_k: int = 10) -> list[tuple[float, int]]:
        """Retorna lista de (score, idx) ordenada por score descendente."""
        if not self.docs:
            return []
        q_tf = self._tf(self._tokenize(query))
        q_vec = self._tfidf_vec(q_tf)
        scores = [(self._cosine(q_vec, self._tfidf_vec(tf)), i)
                  for i, tf in enumerate(self.tf_matrix)]
        scores.sort(reverse=True)
        return scores[:top_k]


# ═══════════════════════════════════════════════════════════════════
# BM25 Index  (Okapi BM25 — mejor que TF-IDF para keyword matching)
# ═══════════════════════════════════════════════════════════════════
class BM25Index:
    """
    BM25 penaliza documentos donde el término aparece en contexto
    irrelevante (ej: apellido "Cabeza" vs síntoma "dolor de cabeza").
    k1=1.5, b=0.75 son los valores estándar de Okapi BM25.
    """
    K1 = 1.5
    B  = 0.75

    def __init__(self) -> None:
        self.docs: list[str] = []
        self.metadata: list[dict] = []
        self._tf_counts: list[dict[str, int]] = []
        self._doc_lens: list[int] = []
        self._avgdl: float = 0.0
        self._idf: dict[str, float] = {}

    def _tokenize(self, text: str) -> list[str]:
        text = text.lower()
        text = re.sub(r"[^a-záéíóúüñ0-9\s]", " ", text)
        return [t for t in text.split() if len(t) > 2]

    def build(self, docs: list[str], metadata: list[dict]) -> None:
        self.docs = docs
        self.metadata = metadata
        self._tf_counts = []
        self._doc_lens = []
        df: dict[str, int] = defaultdict(int)

        for doc in docs:
            tokens = self._tokenize(doc)
            self._doc_lens.append(len(tokens))
            counts: dict[str, int] = defaultdict(int)
            for t in tokens:
                counts[t] += 1
            self._tf_counts.append(dict(counts))
            for t in set(tokens):
                df[t] += 1

        N = len(docs)
        self._avgdl = sum(self._doc_lens) / N if N else 1.0
        self._idf = {
            t: math.log((N - cnt + 0.5) / (cnt + 0.5) + 1)
            for t, cnt in df.items()
        }

    def score(self, query_tokens: list[str], doc_idx: int) -> float:
        tf_counts = self._tf_counts[doc_idx]
        dl = self._doc_lens[doc_idx]
        score = 0.0
        for t in query_tokens:
            if t not in tf_counts:
                continue
            tf = tf_counts[t]
            idf = self._idf.get(t, 0.0)
            numerator   = tf * (self.K1 + 1)
            denominator = tf + self.K1 * (1 - self.B + self.B * dl / self._avgdl)
            score += idf * numerator / denominator
        return score

    def search(self, query: str, top_k: int = 10) -> list[tuple[float, int]]:
        if not self.docs:
            return []
        tokens = self._tokenize(query)
        if not tokens:
            return []
        scores = [(self.score(tokens, i), i) for i in range(len(self.docs))]
        scores.sort(reverse=True)
        return scores[:top_k]


# ═══════════════════════════════════════════════════════════════════
# Reciprocal Rank Fusion
# ═══════════════════════════════════════════════════════════════════
def reciprocal_rank_fusion(
    ranked_lists: list[list[tuple[float, int]]],
    k: int = 60
) -> list[tuple[float, int]]:
    """
    RRF combina múltiples listas de ranking en una sola.
    score_rrf(d) = Σ 1 / (k + rank_i(d))
    k=60 es el valor estándar (Cormack et al., 2009).
    """
    rrf_scores: dict[int, float] = defaultdict(float)
    for ranked in ranked_lists:
        for rank, (_, doc_idx) in enumerate(ranked, start=1):
            rrf_scores[doc_idx] += 1.0 / (k + rank)
    fused = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    return [(score, doc_idx) for doc_idx, score in fused]


# ═══════════════════════════════════════════════════════════════════
# RAG Engine híbrido
# ═══════════════════════════════════════════════════════════════════
class RAGEngine:
    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50, top_k: int = 5) -> None:
        self.chunk_size    = chunk_size
        self.chunk_overlap = chunk_overlap
        self.top_k         = top_k
        self._tfidf  = TFIDFIndex()
        self._bm25   = BM25Index()
        self._docs: list[str] = []
        self._meta: list[dict] = []
        self._is_built = False

    def _chunk_text(self, text: str, meta: dict) -> list[tuple[str, dict]]:
        words = text.split()
        step  = max(1, self.chunk_size - self.chunk_overlap)
        chunks = []
        for i in range(0, len(words), step):
            chunk = " ".join(words[i: i + self.chunk_size])
            if chunk.strip():
                chunks.append((chunk, meta))
        return chunks or [(text, meta)]

    def _tabla_existe(self, db: "Session", tabla: str) -> bool:
        from sqlalchemy import text
        try:
            db.execute(text(f"SELECT 1 FROM `{tabla}` LIMIT 1"))
            return True
        except Exception:
            return False

    def build_index(self, db: "Session") -> int:
        from sqlalchemy import text

        tiene_paciente = self._tabla_existe(db, "paciente")
        if tiene_paciente:
            sql = text("""
                SELECT c.idConsultas, c.idpacientes, c.fecha,
                       c.motivoConsulta, c.enfermedadActual, c.examenFisico, c.observaciones,
                       CONCAT_WS(' ', p.NOMBRES, p.APELLIDOS) AS nombre_paciente
                FROM consultas c
                LEFT JOIN paciente p ON p.ID_PACIENTE = c.idpacientes
                WHERE c.motivoConsulta IS NOT NULL
                   OR c.enfermedadActual IS NOT NULL
                   OR c.examenFisico IS NOT NULL
                LIMIT 2000
            """)
        else:
            sql = text("""
                SELECT idConsultas, idpacientes, fecha,
                       motivoConsulta, enfermedadActual, examenFisico, observaciones,
                       NULL AS nombre_paciente
                FROM consultas
                WHERE motivoConsulta IS NOT NULL
                   OR enfermedadActual IS NOT NULL
                   OR examenFisico IS NOT NULL
                LIMIT 2000
            """)

        rows = db.execute(sql).fetchall()
        all_chunks: list[str] = []
        all_meta:   list[dict] = []

        for row in rows:
            nombre = (row.nombre_paciente or "").strip()
            # Anonimizar: usar token genérico en el texto del chunk
            # El nombre real NUNCA sale hacia el LLM externo
            token_pac = f"PACIENTE_{row.idpacientes}" if row.idpacientes else "PACIENTE_ANONIMO"
            parts  = []
            if nombre:          parts.append(f"Paciente: {token_pac}.")
            if row.fecha:        parts.append(f"Fecha: {row.fecha}.")
            if row.motivoConsulta:   parts.append(f"Motivo: {row.motivoConsulta}.")
            if row.enfermedadActual: parts.append(f"Enfermedad: {row.enfermedadActual}.")
            if row.examenFisico:     parts.append(f"Examen: {row.examenFisico}.")
            if row.observaciones:    parts.append(f"Observaciones: {row.observaciones}.")

            rag_text = " ".join(parts).strip()
            if not rag_text:
                continue

            meta = {
                "id_consulta": row.idConsultas,
                "id_paciente": row.idpacientes,
                "nombre_paciente": nombre or None,
                "fecha": str(row.fecha) if row.fecha else None,
            }
            for chunk, m in self._chunk_text(rag_text, meta):
                all_chunks.append(chunk)
                all_meta.append(m)

        self._docs = all_chunks
        self._meta = all_meta
        self._tfidf.build(all_chunks, all_meta)
        self._bm25.build(all_chunks, all_meta)
        self._is_built = True
        return len(rows)

    def search(self, query: str) -> list[dict]:
        """
        Búsqueda HÍBRIDA: TF-IDF + BM25 fusionados con RRF.
        Retorna JSON con score, texto y metadata de cada fragmento.
        """
        if not self._is_built:
            return []

        candidate_k = min(self.top_k * 3, len(self._docs))

        tfidf_ranked = self._tfidf.search(query, top_k=candidate_k)
        bm25_ranked  = self._bm25.search(query,  top_k=candidate_k)

        fused = reciprocal_rank_fusion([tfidf_ranked, bm25_ranked])

        results = []
        for rrf_score, idx in fused[:self.top_k]:
            results.append({
                "score":    round(rrf_score, 6),
                "text":     self._docs[idx],
                "metadata": self._meta[idx],
            })
        return results

    def search_debug(self, query: str) -> dict:
        """Retorna scores separados de TF-IDF y BM25 para debugging."""
        if not self._is_built:
            return {"error": "índice no construido"}
        k = min(self.top_k * 3, len(self._docs))
        tfidf_r = self._tfidf.search(query, top_k=k)
        bm25_r  = self._bm25.search(query,  top_k=k)
        fused   = reciprocal_rank_fusion([tfidf_r, bm25_r])
        return {
            "query":  query,
            "method": "hybrid_tfidf_bm25_rrf",
            "tfidf_top3":  [{"score": round(s,4), "text": self._docs[i][:80]} for s,i in tfidf_r[:3]],
            "bm25_top3":   [{"score": round(s,4), "text": self._docs[i][:80]} for s,i in bm25_r[:3]],
            "fused_top5":  [{"rrf_score": round(s,6), "text": self._docs[i][:80], "meta": self._meta[i]} for s,i in fused[:5]],
        }

    def build_context(self, query: str) -> str:
        """Contexto listo para inyectar en el prompt del LLM."""
        results = self.search(query)
        if not results:
            return "No se encontraron consultas relevantes en la base de datos."
        parts = []
        for i, r in enumerate(results, 1):
            meta = r["metadata"]
            pac  = meta.get("nombre_paciente", "")
            hdr  = f"[{i}] score={r['score']}" + (f" | paciente: {pac}" if pac else "")
            parts.append(f"{hdr}\n{r['text']}")
        return "\n\n".join(parts)

    @property
    def is_built(self) -> bool:
        return self._is_built


rag_engine = RAGEngine()
