import React, { useState, useEffect } from 'react';
import type { Camera, OperationalStatus } from '../types';
import {
  Plus,
  Trash2,
  MapPin,
  Activity,
  Video,
  Power,
  Loader2,
  RefreshCw,
  AlertTriangle,
  WifiOff,
  PowerOff,
  CheckCircle2,
  AlertOctagon,
  HelpCircle,
  Gauge,
  Layers,
} from 'lucide-react';
import { api } from '../services/api';

interface Props {
  cameras: Camera[];
  onRefreshCameras?: () => void;
  onFullRefresh?: () => void;
  onRefresh?: () => void;
}

const STATUS_CONFIG: Record<
  OperationalStatus,
  { label: string; className: string; icon: React.ComponentType<{ size?: number; className?: string }> }
> = {
  running: { label: 'EN VIVO', className: 'status-running', icon: CheckCircle2 },
  starting: { label: 'INICIANDO', className: 'status-starting', icon: Loader2 },
  reconnecting: { label: 'RECONECTANDO', className: 'status-reconnecting', icon: RefreshCw },
  stalled: { label: 'CONGELADO', className: 'status-stalled', icon: AlertTriangle },
  offline: { label: 'OFFLINE', className: 'status-offline', icon: WifiOff },
  stopped: { label: 'DETENIDO', className: 'status-stopped', icon: PowerOff },
  error: { label: 'ERROR', className: 'status-error', icon: AlertOctagon },
  status_unavailable: { label: 'DESCONOCIDO', className: 'status-unavailable', icon: HelpCircle },
};

const getEffectiveStatus = (cam: Camera): OperationalStatus => {
  if (cam.operational_status) return cam.operational_status;
  return cam.is_active ? 'offline' : 'stopped';
};

const maskRtspUrl = (url?: string | null): string => {
  if (!url) return '';
  return url.replace(/:\/\/(.*?):(.*?)@/, '://$1:****@');
};

export const CamerasView: React.FC<Props> = ({
  cameras,
  onRefreshCameras,
  onFullRefresh,
  onRefresh,
}) => {
  const [showModal, setShowModal] = useState(false);
  const [cameraId, setCameraId] = useState('');
  const [name, setName] = useState('');
  const [location, setLocation] = useState('');
  const [rtspUrl, setRtspUrl] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [togglingId, setTogglingId] = useState<string | null>(null);

  const refreshList = onRefreshCameras || onRefresh;
  const fullRefresh = onFullRefresh || onRefresh || onRefreshCameras;

  // Polling periódico cada 4 segundos exclusivo de cámaras
  useEffect(() => {
    if (!refreshList) return;
    const interval = setInterval(() => {
      refreshList();
    }, 4000);
    return () => clearInterval(interval);
  }, [refreshList]);

  const handleToggle = async (cam: Camera) => {
    try {
      setTogglingId(cam.camera_id);
      await api.toggleCamera(cam.camera_id, !cam.is_active);
      if (refreshList) {
        await refreshList();
      }
    } catch (err: any) {
      alert(`Error al cambiar estado de la cámara ${cam.camera_id}: ${err.message || err}`);
    } finally {
      setTogglingId(null);
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!cameraId.trim() || !name.trim()) return;

    try {
      setIsSubmitting(true);
      await api.createCamera({
        camera_id: cameraId.trim(),
        name: name.trim(),
        location: location.trim() || null,
        rtsp_url: rtspUrl.trim() || null,
        is_active: true,
      });
      setShowModal(false);
      setCameraId('');
      setName('');
      setLocation('');
      setRtspUrl('');
      if (fullRefresh) {
        fullRefresh();
      }
    } catch (err) {
      alert('Error al crear cámara: ' + err);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDelete = async (cid: string) => {
    if (!confirm(`¿Eliminar cámara ${cid}?`)) return;
    try {
      await api.deleteCamera(cid);
      if (fullRefresh) {
        fullRefresh();
      }
    } catch (err) {
      alert('Error al eliminar cámara: ' + err);
    }
  };

  return (
    <div className="cameras-view-container">
      <div className="section-header">
        <div>
          <h2>Cámaras y Puntos de Captura</h2>
          <p className="text-muted-small">
            Supervisión y telemetría de AI Workers en tiempo real (actualización cada 4s)
          </p>
        </div>
        <button className="primary-btn" onClick={() => setShowModal(true)}>
          <Plus size={18} />
          <span>Agregar Cámara</span>
        </button>
      </div>

      <div className="cameras-grid">
        {cameras.map((cam) => {
          const effectiveStatus = getEffectiveStatus(cam);
          const statusCfg = STATUS_CONFIG[effectiveStatus] || STATUS_CONFIG.status_unavailable;
          const StatusIcon = statusCfg.icon;
          const isToggling = togglingId === cam.camera_id;

          return (
            <div key={cam.camera_id} className={`camera-card card-${effectiveStatus}`}>
              <div className="camera-card-top">
                <div className="cam-status-indicator">
                  <span className={`status-badge-chip ${statusCfg.className}`}>
                    <StatusIcon
                      size={12}
                      className={
                        effectiveStatus === 'running'
                          ? 'animate-pulse'
                          : effectiveStatus === 'starting' || effectiveStatus === 'reconnecting'
                          ? 'animate-spin'
                          : ''
                      }
                    />
                    <span>{statusCfg.label}</span>
                  </span>
                  <span className="cam-id-label">{cam.camera_id}</span>
                </div>

                <div className="cam-card-actions">
                  <button
                    className={`btn-icon-toggle ${cam.is_active ? 'toggle-active' : 'toggle-inactive'}`}
                    onClick={() => handleToggle(cam)}
                    disabled={isToggling}
                    title={cam.is_active ? 'Desactivar cámara' : 'Activar cámara'}
                  >
                    {isToggling ? (
                      <Loader2 size={16} className="animate-spin text-cyan" />
                    ) : (
                      <Power size={16} />
                    )}
                  </button>
                  <button
                    className="btn-icon-danger"
                    onClick={() => handleDelete(cam.camera_id)}
                    title="Eliminar Cámara"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>

              <div className="cam-preview-placeholder">
                <Video size={36} className="text-muted" />
                <span>{cam.rtsp_url ? 'RTSP Stream' : 'Sin fuente configurada'}</span>
              </div>

              <div className="camera-card-body">
                <h3 className="camera-name">{cam.name}</h3>

                {cam.location && (
                  <div className="cam-meta-row">
                    <MapPin size={14} className="text-muted" />
                    <span>{cam.location}</span>
                  </div>
                )}

                {cam.rtsp_url && (
                  <div className="cam-meta-row rtsp-row">
                    <Activity size={14} className="text-muted" />
                    <span className="code-text" title={maskRtspUrl(cam.rtsp_url)}>
                      {maskRtspUrl(cam.rtsp_url)}
                    </span>
                  </div>
                )}

                {/* Panel de Telemetría */}
                {cam.telemetry ? (
                  <div className="cam-telemetry-container">
                    <div className="telemetry-grid">
                      <div className="telemetry-item" title="Tasa de procesamiento">
                        <Gauge size={13} className="text-cyan" />
                        <span className="telemetry-val">
                          {(cam.telemetry.fps ?? 0).toFixed(1)} FPS
                        </span>
                      </div>
                      <div className="telemetry-item" title="Cuadros procesados">
                        <Layers size={13} className="text-blue" />
                        <span className="telemetry-val">
                          {(cam.telemetry.frames_processed ?? 0).toLocaleString()} f
                        </span>
                      </div>
                      <div className="telemetry-item" title="Eventos publicados / fallidos hacia Redis">
                        <Activity
                          size={13}
                          className={(cam.telemetry.events_failed ?? 0) > 0 ? 'text-amber' : 'text-green'}
                        />
                        <span className="telemetry-val">
                          {cam.telemetry.events_published ?? 0} pub
                          <span className={(cam.telemetry.events_failed ?? 0) > 0 ? 'text-red' : 'text-muted'}>
                            {' '}/ {cam.telemetry.events_failed ?? 0} err
                          </span>
                        </span>
                      </div>
                      <div className="telemetry-item" title="Reconexiones del worker">
                        <RefreshCw size={13} className="text-amber" />
                        <span className="telemetry-val">
                          {cam.telemetry.reconnect_attempts ?? 0} rec
                        </span>
                      </div>
                    </div>

                    {cam.telemetry.last_error && (
                      <div className="telemetry-error-alert" title={cam.telemetry.last_error}>
                        <AlertOctagon size={13} className="text-red flex-shrink-0" />
                        <span className="telemetry-error-text">{cam.telemetry.last_error}</span>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="telemetry-empty-hint">
                    <span>
                      {cam.is_active
                        ? 'Esperando telemetría del worker...'
                        : 'Worker detenido'}
                    </span>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {showModal && (
        <div className="modal-backdrop">
          <div className="modal-card">
            <div className="modal-header">
              <h3>Registrar Nueva Cámara</h3>
              <button className="close-btn" onClick={() => setShowModal(false)}>
                ×
              </button>
            </div>
            <form onSubmit={handleCreate}>
              <div className="form-group">
                <label>Identificador Único (camera_id)</label>
                <input
                  type="text"
                  required
                  placeholder="ej. cam_acceso_norte"
                  value={cameraId}
                  onChange={(e) => setCameraId(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label>Nombre Descriptivo</label>
                <input
                  type="text"
                  required
                  placeholder="ej. Acceso Peatonal Principal"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label>Ubicación / Sector</label>
                <input
                  type="text"
                  placeholder="ej. Terminal 1 - Puerta 4"
                  value={location}
                  onChange={(e) => setLocation(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label>URL RTSP de Video (Opcional)</label>
                <input
                  type="text"
                  placeholder="rtsp://admin:pass@192.168.1.100:554/stream1"
                  value={rtspUrl}
                  onChange={(e) => setRtspUrl(e.target.value)}
                />
              </div>

              <div className="modal-actions">
                <button
                  type="button"
                  className="secondary-btn"
                  onClick={() => setShowModal(false)}
                >
                  Cancelar
                </button>
                <button type="submit" className="primary-btn" disabled={isSubmitting}>
                  {isSubmitting ? 'Guardando...' : 'Registrar Cámara'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
