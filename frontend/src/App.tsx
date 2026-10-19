import React, { useEffect, useState, useCallback } from 'react';
import { Shield, Camera as CameraIcon, Users, Bell, Radio, RefreshCw } from 'lucide-react';
import { api } from './services/api';
import { useWebSocket } from './hooks/useWebSocket';
import type { AlertEvent, Camera, TargetPerson, SystemStats } from './types';
import { StatsCards } from './components/StatsCards';
import { AlertsFeed } from './components/AlertsFeed';
import { CamerasView } from './components/CamerasView';
import { PersonsView } from './components/PersonsView';

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'alerts' | 'cameras' | 'persons'>('alerts');
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [alerts, setAlerts] = useState<AlertEvent[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [persons, setPersons] = useState<TargetPerson[]>([]);
  const [, setLoading] = useState(true);

  // Carga inicial de datos
  const loadData = useCallback(async () => {
    try {
      const [statsData, alertsData, camerasData, personsData] = await Promise.all([
        api.getStats(),
        api.getAlerts(50),
        api.getCameras(),
        api.getPersons(),
      ]);
      setStats(statsData);
      setAlerts(alertsData);
      setCameras(camerasData);
      setPersons(personsData);
    } catch (err) {
      console.error('Error cargando datos del sistema:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  // Refresco exclusivo de cámaras para polling ligero sin recargar estadísticas ni alertas
  const refreshCameras = useCallback(async () => {
    try {
      const camerasData = await api.getCameras();
      setCameras(camerasData);
    } catch (err) {
      console.error('Error refrescando cámaras:', err);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Manejo de nueva alerta en tiempo real vía WebSocket
  const handleNewAlert = useCallback((newAlert: AlertEvent) => {
    if (!newAlert) return;
    const sanitized: AlertEvent = {
      ...newAlert,
      alert_id: newAlert.alert_id || `alert_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`,
      status: (newAlert.status || 'pending') as any,
      similarity: typeof newAlert.similarity === 'number' ? newAlert.similarity : Number(newAlert.similarity) || 0,
      subject_name: newAlert.subject_name || (newAlert.matched_subject_id != null ? `Sujeto #${newAlert.matched_subject_id}` : 'Desconocido'),
      timestamp: newAlert.timestamp ? Number(newAlert.timestamp) : Date.now() / 1000,
    };
    setAlerts((prev) => [sanitized, ...prev.filter((a) => a.alert_id !== sanitized.alert_id)]);
    setStats((prev) => {
      if (!prev) return null;
      return {
        ...prev,
        total_alerts: (prev.total_alerts || 0) + 1,
        pending_alerts: (prev.pending_alerts || 0) + 1,
      };
    });
  }, []);

  const wsUrl = (() => {
    const protocol = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    if (typeof window !== 'undefined' && (window.location.port === '3000' || window.location.port === '80' || window.location.port === '')) {
      return `${protocol}//${window.location.host}/api/ws/alerts`;
    }
    const host = typeof window !== 'undefined' && window.location.hostname ? window.location.hostname : 'localhost';
    return `${protocol}//${host}:8000/api/ws/alerts`;
  })();

  const { isConnected } = useWebSocket(wsUrl, {
    onNewAlert: handleNewAlert,
  });

  const handleAlertUpdated = (updatedAlert: AlertEvent) => {
    setAlerts((prev) =>
      prev.map((a) => (a.alert_id === updatedAlert.alert_id ? updatedAlert : a))
    );
    // Refrescar estadísticas
    api.getStats().then(setStats).catch(console.error);
  };

  return (
    <div className="app-container">
      {/* Barra superior de navegación y estado */}
      <header className="navbar">
        <div className="navbar-brand">
          <Shield className="brand-logo text-cyan" size={28} />
          <div>
            <h1 className="brand-title">AURA BIOMETRICS</h1>
            <span className="brand-subtitle">Centro de Control y Búsqueda Facial Distribuida</span>
          </div>
        </div>

        <div className="navbar-controls">
          <div className={`ws-status-badge ${isConnected ? 'ws-connected' : 'ws-disconnected'}`}>
            <Radio size={14} className={isConnected ? 'animate-pulse' : ''} />
            <span>{isConnected ? 'EN VIVO (WS CONECTADO)' : 'DESCONECTADO'}</span>
          </div>

          <button className="icon-btn" onClick={loadData} title="Refrescar Datos">
            <RefreshCw size={18} />
          </button>
        </div>
      </header>

      {/* Tarjetas de Métricas del Sistema */}
      <main className="main-content">
        <StatsCards stats={stats} />

        {/* Pestañas de navegación */}
        <div className="tabs-nav">
          <button
            className={`tab-btn ${activeTab === 'alerts' ? 'active' : ''}`}
            onClick={() => setActiveTab('alerts')}
          >
            <Bell size={18} />
            <span>Alertas en Vivo</span>
            {stats && stats.pending_alerts > 0 && (
              <span className="tab-pill">{stats.pending_alerts}</span>
            )}
          </button>

          <button
            className={`tab-btn ${activeTab === 'cameras' ? 'active' : ''}`}
            onClick={() => setActiveTab('cameras')}
          >
            <CameraIcon size={18} />
            <span>Cámaras ({cameras.length})</span>
          </button>

          <button
            className={`tab-btn ${activeTab === 'persons' ? 'active' : ''}`}
            onClick={() => setActiveTab('persons')}
          >
            <Users size={18} />
            <span>Personas Buscadas ({persons.length})</span>
          </button>
        </div>

        {/* Vista activa */}
        <div className="tab-view-content">
          {activeTab === 'alerts' && (
            <AlertsFeed alerts={alerts} onAlertUpdated={handleAlertUpdated} />
          )}
          {activeTab === 'cameras' && (
            <CamerasView
              cameras={cameras}
              onRefreshCameras={refreshCameras}
              onFullRefresh={loadData}
            />
          )}
          {activeTab === 'persons' && (
            <PersonsView persons={persons} onRefresh={loadData} />
          )}
        </div>
      </main>
    </div>
  );
};

export default App;
