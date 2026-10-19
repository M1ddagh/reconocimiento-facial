import React, { useState } from 'react';
import type { AlertEvent } from '../types';
import { CheckCircle2, XCircle, Clock, Video, UserCheck, ShieldAlert } from 'lucide-react';
import { api } from '../services/api';

interface Props {
  alerts: AlertEvent[];
  onAlertUpdated: (updated: AlertEvent) => void;
}

export const AlertsFeed: React.FC<Props> = ({ alerts, onAlertUpdated }) => {
  const [filter, setFilter] = useState<'all' | 'pending' | 'confirmed' | 'dismissed'>('all');
  const [updatingId, setUpdatingId] = useState<string | null>(null);

  const safeAlerts = Array.isArray(alerts) ? alerts : [];

  const filteredAlerts = safeAlerts.filter((a) => {
    if (!a) return false;
    const currentStatus = a.status || 'pending';
    if (filter === 'all') return true;
    return currentStatus === filter;
  });

  const handleUpdateStatus = async (alertId: string, newStatus: string) => {
    if (!alertId) return;
    try {
      setUpdatingId(alertId);
      const updated = await api.updateAlertStatus(alertId, newStatus);
      onAlertUpdated(updated);
    } catch (err) {
      console.error('Error actualizando alerta:', err);
    } finally {
      setUpdatingId(null);
    }
  };

  const formatTimestamp = (ts?: number | string) => {
    if (!ts) return '--:--:--';
    const num = typeof ts === 'number' ? ts : Number(ts);
    if (isNaN(num)) return String(ts);
    // Si la marca de tiempo está en segundos (Unix), multiplicar por 1000
    const ms = num < 1e11 ? num * 1000 : num;
    const d = new Date(ms);
    if (isNaN(d.getTime())) return '--:--:--';
    return (
      d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) +
      ' ' +
      d.toLocaleDateString()
    );
  };

  const getImageUrl = (cropUrl?: string) => {
    if (!cropUrl) return null;
    if (cropUrl.startsWith('http://') || cropUrl.startsWith('https://')) {
      return cropUrl;
    }
    const cleanUrl = cropUrl.startsWith('/') ? cropUrl : `/${cropUrl}`;
    return `http://localhost:8000${cleanUrl}`;
  };

  return (
    <div className="alerts-feed-container">
      <div className="feed-header">
        <div className="feed-title">
          <ShieldAlert className="text-red animate-pulse" size={22} />
          <h2>Transmisión de Alertas en Tiempo Real</h2>
          <span className="badge-counter">{filteredAlerts.length}</span>
        </div>

        <div className="filter-buttons">
          {(['all', 'pending', 'confirmed', 'dismissed'] as const).map((st) => (
            <button
              key={st}
              className={`filter-btn ${filter === st ? 'active' : ''}`}
              onClick={() => setFilter(st)}
            >
              {st === 'all' && 'Todas'}
              {st === 'pending' && 'Pendientes'}
              {st === 'confirmed' && 'Confirmadas'}
              {st === 'dismissed' && 'Descartadas'}
            </button>
          ))}
        </div>
      </div>

      {filteredAlerts.length === 0 ? (
        <div className="empty-state">
          <ShieldAlert size={48} className="text-muted mb-2" />
          <p>No hay alertas para mostrar en este momento.</p>
          <span className="text-muted-small">Las detecciones de coincidencia aparecerán automáticamente en tiempo real.</span>
        </div>
      ) : (
        <div className="alerts-list">
          {filteredAlerts.map((alert, idx) => {
            const status = (alert.status || 'pending').toLowerCase();
            const rawSim = typeof alert.similarity === 'number' ? alert.similarity : Number(alert.similarity) || 0;
            const simVal = Math.max(0, Math.min(1, rawSim));
            const similarityPercent = Math.round(simVal * 100);
            const isHighConfidence = simVal >= 0.8;
            const uniqueKey = alert.alert_id || (alert.id != null ? `id_${alert.id}` : `alert_idx_${idx}`);
            const resolvedCropUrl = getImageUrl(alert.crop_url);

            return (
              <div
                key={uniqueKey}
                className={`alert-card ${status === 'pending' ? 'border-pending' : ''}`}
              >
                <div className="alert-media">
                  {alert.crop_image_base64 ? (
                    <img
                      src={
                        alert.crop_image_base64.startsWith('data:')
                          ? alert.crop_image_base64
                          : `data:image/jpeg;base64,${alert.crop_image_base64}`
                      }
                      alt="Crop de detección"
                      className="crop-image"
                    />
                  ) : resolvedCropUrl ? (
                    <img
                      src={resolvedCropUrl}
                      alt="Crop de detección"
                      className="crop-image"
                      onError={(e) => {
                        (e.target as HTMLElement).style.display = 'none';
                      }}
                    />
                  ) : (
                    <div className="crop-placeholder">
                      <UserCheck size={36} className="text-muted" />
                    </div>
                  )}
                  <span className={`sim-badge ${isHighConfidence ? 'sim-high' : 'sim-medium'}`}>
                    {similarityPercent}% match
                  </span>
                </div>

                <div className="alert-content">
                  <div className="alert-top">
                    <h3 className="subject-name">
                      {alert.subject_name || (alert.matched_subject_id != null ? `Sujeto #${alert.matched_subject_id}` : 'Desconocido')}
                    </h3>
                    <span className={`status-pill status-${status}`}>
                      {status.toUpperCase()}
                    </span>
                  </div>

                  <div className="alert-meta-grid">
                    <div className="meta-item">
                      <Video size={14} className="text-muted" />
                      <span>{alert.camera_id || 'Cámara'}</span>
                    </div>
                    <div className="meta-item">
                      <Clock size={14} className="text-muted" />
                      <span>{formatTimestamp(alert.timestamp)}</span>
                    </div>
                  </div>

                  <div className="confidence-bar-container">
                    <div className="bar-labels">
                      <span>Similitud ArcFace (512D)</span>
                      <span>{simVal.toFixed(4)}</span>
                    </div>
                    <div className="progress-bar-bg">
                      <div
                        className={`progress-bar-fill ${isHighConfidence ? 'fill-high' : 'fill-medium'}`}
                        style={{ width: `${Math.min(100, similarityPercent)}%` }}
                      />
                    </div>
                  </div>
                </div>

                <div className="alert-actions">
                  <button
                    className="action-btn btn-confirm"
                    title="Confirmar Coincidencia"
                    disabled={updatingId === alert.alert_id || status === 'confirmed'}
                    onClick={() => alert.alert_id && handleUpdateStatus(alert.alert_id, 'confirmed')}
                  >
                    <CheckCircle2 size={18} />
                    <span>Confirmar</span>
                  </button>

                  <button
                    className="action-btn btn-dismiss"
                    title="Descartar Falso Positivo"
                    disabled={updatingId === alert.alert_id || status === 'dismissed'}
                    onClick={() => alert.alert_id && handleUpdateStatus(alert.alert_id, 'dismissed')}
                  >
                    <XCircle size={18} />
                    <span>Descartar</span>
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
