#!/usr/bin/env python3
from __future__ import annotations

"""
search_service/vector_indexer.py
--------------------------------
Motor de indexación y búsqueda vectorial con FAISS (IndexFlatIP + IndexIDMap2).
Optimizado para vectores unitarios ArcFace de 512 dimensiones (Similitud Coseno).
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional, Union

import faiss
import numpy as np

logger = logging.getLogger("search_service.vector_indexer")

VECTOR_DIMENSION = 512
DEFAULT_SIMILARITY_THRESHOLD = 0.65


class FaceSearchEngine:
    """
    Motor vectorial FAISS para gestión de identidades y búsqueda biométrica de alta velocidad.
    """

    def __init__(
        self,
        index_path: Union[str, Path] = "search_service/faces.index",
        dimension: int = VECTOR_DIMENSION,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ):
        self.index_path = Path(index_path)
        self.dimension = dimension
        self.similarity_threshold = similarity_threshold
        self.metadata_path = self.index_path.with_suffix(".json")

        self.index: faiss.IndexIDMap2 = self._load_or_create_index()
        self.metadata: dict[int, dict] = self._load_metadata()

    def _load_or_create_index(self) -> faiss.IndexIDMap2:
        if self.index_path.is_file():
            try:
                idx = faiss.read_index(str(self.index_path))
                logger.info(f"[✓] Índice FAISS cargado exitosamente desde '{self.index_path}'. Total identidades: {idx.ntotal}")
                return idx
            except Exception as e:
                logger.warning(f"[!] No fue posible leer el índice '{self.index_path}': {e}. Creando nuevo índice...")

        base_index = faiss.IndexFlatIP(self.dimension)
        idx = faiss.IndexIDMap2(base_index)
        logger.info(f"[+] Nuevo índice FAISS inicializado (FlatIP, dim={self.dimension}).")
        return idx

    def _load_metadata(self) -> dict[int, dict]:
        """Carga el diccionario de metadatos (ID -> Nombre, notas, etc.)."""
        if self.metadata_path.is_file():
            try:
                with open(self.metadata_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Convertir llaves a enteros
                    return {int(k): v for k, v in data.items()}
            except Exception as err:
                logger.warning(f"No se pudo cargar metadata '{self.metadata_path}': {err}")
        return {}

    def save(self) -> None:
        """Persiste el índice binario FAISS y los metadatos JSON a disco."""
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(self.index_path))
        with open(self.metadata_path, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in self.metadata.items()}, f, indent=2, ensure_ascii=False)
        logger.info(f"[✓] Índice y metadatos guardados ({self.index.ntotal} vectores).")

    def reload(self) -> None:
        """Recarga el índice FAISS y los metadatos desde el disco."""
        self.index = self._load_or_create_index()
        self.metadata = self._load_metadata()
        logger.info(f"[✓] Índice FAISS recargado dinámicamente ({self.index.ntotal} vectores).")

    def reset(self) -> None:
        """Reinicia el índice y los metadatos dejándolos completamente vacíos."""
        base_index = faiss.IndexFlatIP(self.dimension)
        self.index = faiss.IndexIDMap2(base_index)
        self.metadata = {}
        self.save()
        logger.info("[✓] Índice FAISS y metadatos reiniciados a cero.")

    @staticmethod
    def normalize_l2(vectors: np.ndarray) -> np.ndarray:
        vectors = np.ascontiguousarray(vectors, dtype=np.float32)
        if vectors.ndim == 1:
            vectors = np.expand_dims(vectors, axis=0)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1e-12, norms)
        return vectors / norms

    def add_identity(
        self,
        subject_id: int,
        embedding: Union[np.ndarray, list[float]],
        name: str,
        extra_meta: Optional[dict] = None,
        auto_save: bool = True,
    ) -> None:
        """
        Registra una nueva identidad conocida en el índice.
        """
        emb_arr = np.array(embedding, dtype=np.float32)
        norm_emb = self.normalize_l2(emb_arr)
        id_arr = np.array([subject_id], dtype=np.int64)

        # Si el ID ya existe en el índice, removerlo previamente para evitar duplicados
        try:
            self.index.remove_ids(id_arr)
        except Exception:
            pass

        self.index.add_with_ids(norm_emb, id_arr)

        meta = {
            "name": name,
            "created_at": time.time(),
        }
        if extra_meta:
            meta.update(extra_meta)
        self.metadata[subject_id] = meta

        if auto_save:
            self.save()

        logger.info(f"[+] Sujeto #{subject_id} ('{name}') indexado con éxito.")

    def search(
        self,
        query_vector: Union[np.ndarray, list[float]],
        k: int = 1,
        threshold: Optional[float] = None,
    ) -> dict:
        """
        Ejecuta la búsqueda vectorial por similitud coseno (1-NN o k-NN).
        """
        if threshold is None:
            threshold = self.similarity_threshold

        emb_arr = np.array(query_vector, dtype=np.float32)
        norm_query = self.normalize_l2(emb_arr)

        if self.index.ntotal == 0:
            return {
                "is_match": False,
                "matched_id": None,
                "subject_name": None,
                "similarity": 0.0,
                "search_time_ms": 0.0,
                "metadata": None,
                "status": "INDICE_VACIO",
            }

        start_time = time.perf_counter()
        scores, indices = self.index.search(norm_query, k)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        top_id = int(indices[0][0])
        top_score = float(scores[0][0])
        clamped_score = max(0.0, min(1.0, top_score))

        is_match = (top_id != -1) and (clamped_score >= threshold)
        subject_info = self.metadata.get(top_id) if is_match else None
        subject_name = subject_info.get("name") if subject_info else None

        return {
            "is_match": is_match,
            "matched_id": top_id if is_match else None,
            "candidate_id": top_id,
            "subject_name": subject_name,
            "similarity": clamped_score,
            "threshold": threshold,
            "search_time_ms": elapsed_ms,
            "metadata": subject_info,
            "status": "MATCH" if is_match else "DESCONOCIDO",
        }
