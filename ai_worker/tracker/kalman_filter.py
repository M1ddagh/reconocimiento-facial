#!/usr/bin/env python3
from __future__ import annotations

"""
ai_worker/tracker/kalman_filter.py
-----------------------------------
Filtro de Kalman para el seguimiento de cajas delimitadoras en espacio de estados 8D:
[x, y, a, h, vx, vy, va, vh]
Donde:
  - (x, y): Centro de la caja delimitadora
  - a: Relación de aspecto (ancho / alto)
  - h: Altura de la caja
  - vx, vy, va, vh: Velocidades asociadas
"""

import numpy as np


class KalmanFilter:
    """
    Filtro de Kalman lineal con modelo de velocidad constante para seguimiento 2D.
    """

    def __init__(self) -> None:
        ndim = 4
        dt = 1.0

        # Matriz de transición de estado F (8x8)
        self._motion_mat = np.eye(2 * ndim, 2 * ndim, dtype=np.float32)
        for i in range(ndim):
            self._motion_mat[i, ndim + i] = dt

        # Matriz de observación H (4x8)
        self._update_mat = np.eye(ndim, 2 * ndim, dtype=np.float32)

        # Pesos de incertidumbre relativa respecto a la altura
        self._std_weight_position = 1.0 / 20
        self._std_weight_velocity = 1.0 / 160

    def initiate(self, measurement: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Crea un nuevo estado a partir de una medición no asociada [x, y, a, h].
        """
        mean_pos = measurement
        mean_vel = np.zeros_like(mean_pos)
        mean = np.r_[mean_pos, mean_vel]

        std = [
            2 * self._std_weight_position * measurement[3],
            2 * self._std_weight_position * measurement[3],
            1e-2,
            2 * self._std_weight_position * measurement[3],
            10 * self._std_weight_velocity * measurement[3],
            10 * self._std_weight_velocity * measurement[3],
            1e-5,
            10 * self._std_weight_velocity * measurement[3],
        ]
        covariance = np.diag(np.square(std)).astype(np.float32)
        return mean.astype(np.float32), covariance

    def predict(self, mean: np.ndarray, covariance: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Paso de predicción temporal del Filtro de Kalman.
        """
        std_pos = [
            self._std_weight_position * mean[3],
            self._std_weight_position * mean[3],
            1e-2,
            self._std_weight_position * mean[3],
        ]
        std_vel = [
            self._std_weight_velocity * mean[3],
            self._std_weight_velocity * mean[3],
            1e-5,
            self._std_weight_velocity * mean[3],
        ]
        motion_cov = np.diag(np.square(np.r_[std_pos, std_vel])).astype(np.float32)

        mean = np.dot(self._motion_mat, mean)
        covariance = (
            np.linalg.multi_dot((self._motion_mat, covariance, self._motion_mat.T))
            + motion_cov
        )
        return mean, covariance

    def project(self, mean: np.ndarray, covariance: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Proyecta la distribución de estado al espacio de medición (4D).
        """
        std = [
            self._std_weight_position * mean[3],
            self._std_weight_position * mean[3],
            1e-1,
            self._std_weight_position * mean[3],
        ]
        innovation_cov = np.diag(np.square(std)).astype(np.float32)

        mean = np.dot(self._update_mat, mean)
        covariance = (
            np.linalg.multi_dot((self._update_mat, covariance, self._update_mat.T))
            + innovation_cov
        )
        return mean, covariance

    def update(
        self, mean: np.ndarray, covariance: np.ndarray, measurement: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Paso de corrección / actualización con una nueva medición [x, y, a, h].
        """
        projected_mean, projected_cov = self.project(mean, covariance)

        # Ganancia de Kalman K = P * H^T * (H * P * H^T + R)^(-1)
        chol_factor, lower = np.linalg.cholesky(projected_cov), True
        kalman_gain = np.linalg.solve(
            chol_factor,
            np.dot(covariance, self._update_mat.T).T,
        ).T
        kalman_gain = np.linalg.solve(chol_factor.T, kalman_gain.T).T

        innovation = measurement - projected_mean
        new_mean = mean + np.dot(innovation, kalman_gain.T)
        new_covariance = covariance - np.linalg.multi_dot(
            (kalman_gain, projected_cov, kalman_gain.T)
        )
        return new_mean, new_covariance
