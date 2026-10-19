#!/usr/bin/env python3
from __future__ import annotations

"""
ai_worker/tracker/matching.py
-----------------------------
Módulo de cálculo de IoU y asociación de grafos bipartitos (Algoritmo Húngaro / linear_sum_assignment)
para emparejar detecciones con pistas activas en el tracker.
"""

import numpy as np
from scipy.optimize import linear_sum_assignment


def bbox_iou(boxes1: np.ndarray, boxes2: np.ndarray) -> np.ndarray:
    """
    Calcula la matriz de Intersección sobre Unión (IoU) entre dos conjuntos de cajas.
    Formato esperado: [x1, y1, x2, y2].
    
    Retorna:
      Matriz de tamaño (N, M) con valores en [0, 1].
    """
    if len(boxes1) == 0 or len(boxes2) == 0:
        return np.zeros((len(boxes1), len(boxes2)), dtype=np.float32)

    boxes1 = np.ascontiguousarray(boxes1, dtype=np.float32)
    boxes2 = np.ascontiguousarray(boxes2, dtype=np.float32)

    area1 = (boxes1[:, 2] - boxes1[:, 0]) * (boxes1[:, 3] - boxes1[:, 1])
    area2 = (boxes2[:, 2] - boxes2[:, 0]) * (boxes2[:, 3] - boxes2[:, 1])

    # Coordenadas de la intersección
    lt = np.maximum(boxes1[:, None, :2], boxes2[None, :, :2])  # [N, M, 2]
    rb = np.minimum(boxes1[:, None, 2:], boxes2[None, :, 2:])  # [N, M, 2]

    wh = np.clip(rb - lt, a_min=0, a_max=None)  # [N, M, 2]
    intersection = wh[:, :, 0] * wh[:, :, 1]  # [N, M]

    union = area1[:, None] + area2[None, :] - intersection
    union = np.clip(union, a_min=1e-7, a_max=None)

    return intersection / union


def iou_distance(tracks: list, detections: list) -> np.ndarray:
    """
    Calcula la matriz de costo de distancia de IoU (1.0 - IoU).
    """
    if len(tracks) == 0 or len(detections) == 0:
        return np.zeros((len(tracks), len(detections)), dtype=np.float32)

    track_boxes = np.array([t.tlbr for t in tracks], dtype=np.float32)
    det_boxes = np.array([d.tlbr for d in detections], dtype=np.float32)

    ious = bbox_iou(track_boxes, det_boxes)
    return 1.0 - ious


def linear_assignment(
    cost_matrix: np.ndarray, thresh: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Asociación de pares detección-track mediante asignación lineal de costo mínimo.
    """
    if cost_matrix.size == 0:
        return (
            np.empty((0, 2), dtype=int),
            np.arange(cost_matrix.shape[0], dtype=int),
            np.arange(cost_matrix.shape[1], dtype=int),
        )

    row_indices, col_indices = linear_sum_assignment(cost_matrix)

    matches = []
    unmatched_a = list(range(cost_matrix.shape[0]))
    unmatched_b = list(range(cost_matrix.shape[1]))

    for r, c in zip(row_indices, col_indices):
        if cost_matrix[r, c] <= thresh:
            matches.append([r, c])
            if r in unmatched_a:
                unmatched_a.remove(r)
            if c in unmatched_b:
                unmatched_b.remove(c)

    return (
        np.array(matches, dtype=int) if len(matches) > 0 else np.empty((0, 2), dtype=int),
        np.array(unmatched_a, dtype=int),
        np.array(unmatched_b, dtype=int),
    )
