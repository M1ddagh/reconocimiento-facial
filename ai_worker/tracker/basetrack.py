#!/usr/bin/env python3
from __future__ import annotations

"""
ai_worker/tracker/basetrack.py
------------------------------
Estructuras base para pistas (tracks), estados de ciclo de vida (TrackState)
y representación de pista individual (STrack).
"""

from enum import Enum
import numpy as np
from ai_worker.tracker.kalman_filter import KalmanFilter


class TrackState(Enum):
    New = 0
    Tracked = 1
    Lost = 2
    Removed = 3


class BaseTrack:
    _count = 0

    track_id = 0
    is_activated = False
    state = TrackState.New

    history: dict = {}
    features: list = []
    curr_feature: list = []
    score = 0.0
    start_frame = 0
    frame_id = 0
    time_since_update = 0

    @property
    def end_frame(self) -> int:
        return self.frame_id

    @classmethod
    def next_id(cls) -> int:
        cls._count += 1
        return cls._count

    @classmethod
    def reset_counter(cls) -> None:
        cls._count = 0

    def activate(self, *args):
        raise NotImplementedError

    def predict(self):
        raise NotImplementedError

    def update(self, *args, **kwargs):
        raise NotImplementedError

    def mark_lost(self):
        self.state = TrackState.Lost

    def mark_removed(self):
        self.state = TrackState.Removed


class STrack(BaseTrack):
    shared_kalman = KalmanFilter()

    def __init__(self, tlwh: np.ndarray, score: float, face_data: dict | None = None):
        """
        tlwh: [top_left_x, top_left_y, width, height]
        score: confianza de detección
        face_data: metadatos opcionales (embedding, landmarks, etc.)
        """
        # Formato interno de caja: tlwh
        self._tlwh = np.asarray(tlwh, dtype=np.float32)
        self.kalman_filter: KalmanFilter | None = None
        self.mean: np.ndarray | None = None
        self.covariance: np.ndarray | None = None
        self.is_activated = False

        self.score = float(score)
        self.tracklet_len = 0
        self.face_data = face_data or {}

    @property
    def tlwh(self) -> np.ndarray:
        if self.mean is None:
            return self._tlwh.copy()
        ret = self.mean[:4].copy()
        ret[2] *= ret[3]
        ret[:2] -= ret[2:] / 2
        return ret

    @property
    def tlbr(self) -> np.ndarray:
        """Convierte [top, left, width, height] a [x1, y1, x2, y2]."""
        ret = self.tlwh
        ret[2:] += ret[:2]
        return ret

    @staticmethod
    def tlwh_to_xyah(tlwh: np.ndarray) -> np.ndarray:
        """Convierte [x, y, w, h] a [center_x, center_y, aspect_ratio, h]."""
        ret = np.asarray(tlwh, dtype=np.float32).copy()
        ret[:2] += ret[2:] / 2
        ret[2] /= max(1e-5, ret[3])
        return ret

    @staticmethod
    def tlbr_to_tlwh(tlbr: np.ndarray) -> np.ndarray:
        ret = np.asarray(tlbr, dtype=np.float32).copy()
        ret[2:] -= ret[:2]
        return ret

    def to_xyah(self) -> np.ndarray:
        return self.tlwh_to_xyah(self.tlwh)

    def activate(self, kalman_filter: KalmanFilter, frame_id: int):
        """Activa una nueva pista iniciándola con el filtro de Kalman."""
        self.kalman_filter = kalman_filter
        self.track_id = self.next_id()
        self.mean, self.covariance = self.kalman_filter.initiate(
            self.tlwh_to_xyah(self._tlwh)
        )
        self.tracklet_len = 0
        self.state = TrackState.Tracked
        if frame_id == 1:
            self.is_activated = True
        self.frame_id = frame_id
        self.start_frame = frame_id

    def re_activate(self, new_track: STrack, frame_id: int, new_id: bool = False):
        self.mean, self.covariance = self.kalman_filter.update(
            self.mean, self.covariance, self.tlwh_to_xyah(new_track.tlwh)
        )
        self.tracklet_len = 0
        self.state = TrackState.Tracked
        self.is_activated = True
        self.frame_id = frame_id
        if new_id:
            self.track_id = self.next_id()
        self.score = new_track.score
        if new_track.face_data:
            self.face_data = new_track.face_data

    def update(self, new_track: STrack, frame_id: int):
        """Actualiza la pista asociada con una nueva detección."""
        self.frame_id = frame_id
        self.tracklet_len += 1

        new_tlwh = new_track.tlwh
        self.mean, self.covariance = self.kalman_filter.update(
            self.mean, self.covariance, self.tlwh_to_xyah(new_tlwh)
        )
        self.state = TrackState.Tracked
        self.is_activated = True
        self.score = new_track.score
        if new_track.face_data:
            self.face_data = new_track.face_data

    @staticmethod
    def multi_predict(stracks: list[STrack]):
        if len(stracks) > 0:
            for st in stracks:
                if st.state != TrackState.Tracked:
                    st.mean[7] = 0
                st.mean, st.covariance = st.kalman_filter.predict(st.mean, st.covariance)
