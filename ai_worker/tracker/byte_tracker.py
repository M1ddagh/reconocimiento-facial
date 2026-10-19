#!/usr/bin/env python3
from __future__ import annotations

"""
ai_worker/tracker/byte_tracker.py
---------------------------------
Implementación pura y desacoplada de ByteTrack (Zhang et al.) para seguimiento
multi-objeto de rostros en video en tiempo real.
"""

from typing import List, Tuple
import numpy as np

from ai_worker.tracker.basetrack import BaseTrack, STrack, TrackState
from ai_worker.tracker.kalman_filter import KalmanFilter
from ai_worker.tracker.matching import iou_distance, linear_assignment


def joint_stracks(tlista: list[STrack], tlistb: list[STrack]) -> list[STrack]:
    exists = {}
    res = []
    for t in tlista:
        exists[t.track_id] = 1
        res.append(t)
    for t in tlistb:
        tid = t.track_id
        if not exists.get(tid, 0):
            exists[tid] = 1
            res.append(t)
    return res


def sub_stracks(tlista: list[STrack], tlistb: list[STrack]) -> list[STrack]:
    stracks = {}
    for t in tlista:
        stracks[t.track_id] = t
    for t in tlistb:
        tid = t.track_id
        if stracks.get(tid, 0):
            del stracks[tid]
    return list(stracks.values())


def remove_duplicate_stracks(stracksa: list[STrack], stracksb: list[STrack]) -> tuple[list[STrack], list[STrack]]:
    pdist = iou_distance(stracksa, stracksb)
    pairs = np.where(pdist < 0.15)
    dupa, dupb = list(), list()
    for p, q in zip(*pairs):
        timep = stracksa[p].frame_id - stracksa[p].start_frame
        timeq = stracksb[q].frame_id - stracksb[q].start_frame
        if timep > timeq:
            dupb.append(q)
        else:
            dupa.append(p)
    resa = [t for i, t in enumerate(stracksa) if i not in dupa]
    resb = [t for i, t in enumerate(stracksb) if i not in dupb]
    return resa, resb


class ByteTracker:
    """
    Rastreador ByteTrack optimizado para detección y seguimiento de rostros.
    """

    def __init__(
        self,
        track_thresh: float = 0.5,
        match_thresh: float = 0.8,
        track_buffer: int = 30,
        frame_rate: int = 30,
    ):
        self.tracked_stracks: list[STrack] = []
        self.lost_stracks: list[STrack] = []
        self.removed_stracks: list[STrack] = []

        self.frame_id = 0
        self.track_thresh = track_thresh
        self.match_thresh = match_thresh
        self.det_thresh = track_thresh / 2.0
        self.buffer_size = int(frame_rate / 30.0 * track_buffer)
        self.max_time_lost = self.buffer_size
        self.kalman_filter = KalmanFilter()

    def reset(self) -> None:
        """Reinicia el estado del tracker."""
        self.tracked_stracks.clear()
        self.lost_stracks.clear()
        self.removed_stracks.clear()
        self.frame_id = 0
        BaseTrack.reset_counter()

    def update(self, output_results: list[dict]) -> list[STrack]:
        """
        output_results: Lista de detecciones de rostros en el frame actual.
        Cada elemento contiene:
          - 'bbox': [x1, y1, x2, y2]
          - 'score': float (confianza)
          - 'face_data': dict (embedding, landmarks, etc.)
        """
        self.frame_id += 1
        activated_starcks = []
        refind_stracks = []
        lost_stracks = []
        removed_stracks = []

        if len(output_results) > 0:
            bboxes = np.array([res["bbox"] for res in output_results], dtype=np.float32)
            scores = np.array([res["score"] for res in output_results], dtype=np.float32)
            face_datas = [res.get("face_data", {}) for res in output_results]

            remain_inds = scores >= self.track_thresh
            inds_low = scores > self.det_thresh
            inds_high = remain_inds
            inds_second = np.logical_and(inds_low, ~remain_inds)

            dets_second = bboxes[inds_second]
            dets = bboxes[inds_high]
            scores_keep = scores[inds_high]
            scores_second = scores[inds_second]
            data_keep = [d for d, k in zip(face_datas, inds_high) if k]
            data_second = [d for d, s in zip(face_datas, inds_second) if s]
        else:
            dets = np.empty((0, 4), dtype=np.float32)
            scores_keep = np.empty((0,), dtype=np.float32)
            dets_second = np.empty((0, 4), dtype=np.float32)
            scores_second = np.empty((0,), dtype=np.float32)
            data_keep = []
            data_second = []

        if len(dets) > 0:
            # Convertir bboxes tlbr a tlwh
            tlwhs = dets.copy()
            tlwhs[:, 2:] -= tlwhs[:, :2]
            detections = [
                STrack(tlwh, s, d) for (tlwh, s, d) in zip(tlwhs, scores_keep, data_keep)
            ]
        else:
            detections = []

        # Separar pistas no confirmadas y confirmadas
        unconfirmed = []
        tracked_stracks = []
        for track in self.tracked_stracks:
            if not track.is_activated:
                unconfirmed.append(track)
            else:
                tracked_stracks.append(track)

        # Paso 1: Predicción de Kalman para pistas activas y perdidas
        strack_pool = joint_stracks(tracked_stracks, self.lost_stracks)
        STrack.multi_predict(strack_pool)

        # Paso 2: Primera asociación con detecciones de alta confianza
        dists = iou_distance(strack_pool, detections)
        matches, u_track, u_detection = linear_assignment(dists, thresh=self.match_thresh)

        for itracked, idet in matches:
            track = strack_pool[itracked]
            det = detections[idet]
            if track.state == TrackState.Tracked:
                track.update(det, self.frame_id)
                activated_starcks.append(track)
            else:
                track.re_activate(det, self.frame_id, new_id=False)
                refind_stracks.append(track)

        # Paso 3: Segunda asociación con detecciones de baja confianza
        if len(dets_second) > 0:
            tlwhs_second = dets_second.copy()
            tlwhs_second[:, 2:] -= tlwhs_second[:, :2]
            detections_second = [
                STrack(tlwh, s, d)
                for (tlwh, s, d) in zip(tlwhs_second, scores_second, data_second)
            ]
        else:
            detections_second = []

        r_tracked_stracks = [
            strack_pool[i]
            for i in u_track
            if strack_pool[i].state == TrackState.Tracked
        ]
        dists = iou_distance(r_tracked_stracks, detections_second)
        matches, u_track, u_detection_second = linear_assignment(dists, thresh=0.5)

        for itracked, idet in matches:
            track = r_tracked_stracks[itracked]
            det = detections_second[idet]
            if track.state == TrackState.Tracked:
                track.update(det, self.frame_id)
                activated_starcks.append(track)
            else:
                track.re_activate(det, self.frame_id, new_id=False)
                refind_stracks.append(track)

        for it in u_track:
            track = r_tracked_stracks[it]
            if not track.state == TrackState.Lost:
                track.mark_lost()
                lost_stracks.append(track)

        # Paso 4: Asociación con pistas no confirmadas
        detections = [detections[i] for i in u_detection]
        dists = iou_distance(unconfirmed, detections)
        matches, u_unconfirmed, u_detection = linear_assignment(dists, thresh=0.7)
        for itracked, idet in matches:
            unconfirmed[itracked].update(detections[idet], self.frame_id)
            activated_starcks.append(unconfirmed[itracked])
        for it in u_unconfirmed:
            track = unconfirmed[it]
            track.mark_removed()
            removed_stracks.append(track)

        # Paso 5: Inicializar nuevas pistas con detecciones de alta confianza no emparejadas
        for inew in u_detection:
            track = detections[inew]
            if track.score < self.track_thresh:
                continue
            track.activate(self.kalman_filter, self.frame_id)
            activated_starcks.append(track)

        # Paso 6: Actualizar pistas perdidas y remover las obsoletas
        for track in self.lost_stracks:
            if self.frame_id - track.end_frame > self.max_time_lost:
                track.mark_removed()
                removed_stracks.append(track)

        self.tracked_stracks = [
            t for t in self.tracked_stracks if t.state == TrackState.Tracked
        ]
        self.tracked_stracks = joint_stracks(self.tracked_stracks, activated_starcks)
        self.tracked_stracks = joint_stracks(self.tracked_stracks, refind_stracks)
        self.lost_stracks = sub_stracks(self.lost_stracks, self.tracked_stracks)
        self.lost_stracks.extend(lost_stracks)
        self.lost_stracks = sub_stracks(self.lost_stracks, self.removed_stracks)
        self.removed_stracks.extend(removed_stracks)
        self.tracked_stracks, self.lost_stracks = remove_duplicate_stracks(
            self.tracked_stracks, self.lost_stracks
        )

        # Retornar sólo las pistas que están activadas y actualmente en seguimiento
        output_stracks = [track for track in self.tracked_stracks if track.is_activated]
        return output_stracks
