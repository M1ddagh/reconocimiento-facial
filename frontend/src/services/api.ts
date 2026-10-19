import type { Camera, CameraUpdatePayload, TargetPerson, AlertEvent, SystemStats } from '../types';

const getApiBase = () => {
  if (typeof window !== 'undefined' && (window.location.port === '3000' || window.location.port === '80' || window.location.port === '')) {
    return '/api';
  }
  const host = (typeof window !== 'undefined' && window.location.hostname) ? window.location.hostname : 'localhost';
  return `http://${host}:8000/api`;
};

const API_BASE = getApiBase();

export const api = {
  // Stats
  async getStats(): Promise<SystemStats> {
    const res = await fetch(`${API_BASE}/stats`);
    if (!res.ok) throw new Error('Error al obtener estadísticas');
    return res.json();
  },

  // Alertas
  async getAlerts(limit = 50): Promise<AlertEvent[]> {
    const res = await fetch(`${API_BASE}/alerts?limit=${limit}`);
    if (!res.ok) throw new Error('Error al obtener alertas');
    return res.json();
  },

  async updateAlertStatus(alertId: string, status: string): Promise<AlertEvent> {
    const res = await fetch(`${API_BASE}/alerts/${alertId}/status`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    if (!res.ok) throw new Error('Error al actualizar alerta');
    return res.json();
  },

  // Cámaras
  async getCameras(): Promise<Camera[]> {
    const res = await fetch(`${API_BASE}/cameras`);
    if (!res.ok) throw new Error('Error al obtener cámaras');
    return res.json();
  },

  async createCamera(camera: Omit<Camera, 'id' | 'created_at' | 'operational_status' | 'telemetry'>): Promise<Camera> {
    const res = await fetch(`${API_BASE}/cameras`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(camera),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Error al crear cámara');
    }
    return res.json();
  },

  async updateCamera(cameraId: string, payload: CameraUpdatePayload): Promise<Camera> {
    const res = await fetch(`${API_BASE}/cameras/${cameraId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Error al actualizar cámara');
    }
    return res.json();
  },

  async toggleCamera(cameraId: string, isActive: boolean): Promise<Camera> {
    return this.updateCamera(cameraId, { is_active: isActive });
  },

  async deleteCamera(cameraId: string): Promise<void> {
    const res = await fetch(`${API_BASE}/cameras/${cameraId}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error('Error al eliminar cámara');
  },

  // Personas Buscadas
  async getPersons(): Promise<TargetPerson[]> {
    const res = await fetch(`${API_BASE}/persons`);
    if (!res.ok) throw new Error('Error al obtener personas');
    return res.json();
  },

  async createPerson(formData: FormData): Promise<TargetPerson> {
    const res = await fetch(`${API_BASE}/persons`, {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) throw new Error('Error al registrar persona');
    return res.json();
  },

  async deletePerson(personId: number): Promise<void> {
    const res = await fetch(`${API_BASE}/persons/${personId}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error('Error al eliminar persona');
  },
};
