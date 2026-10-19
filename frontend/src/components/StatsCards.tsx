import React from 'react';
import { Camera, Users, AlertTriangle, Database } from 'lucide-react';
import type { SystemStats } from '../types';

interface Props {
  stats: SystemStats | null;
}

export const StatsCards: React.FC<Props> = ({ stats }) => {
  return (
    <div className="stats-grid">
      <div className="stat-card">
        <div className="stat-header">
          <span className="stat-label">Cámaras Activas</span>
          <Camera className="stat-icon text-cyan" size={20} />
        </div>
        <div className="stat-value">
          {stats ? `${stats.active_cameras} / ${stats.total_cameras}` : '--'}
        </div>
        <div className="stat-sub">Flujos en ingesta activa</div>
      </div>

      <div className="stat-card">
        <div className="stat-header">
          <span className="stat-label">Personas Buscadas</span>
          <Users className="stat-icon text-blue" size={20} />
        </div>
        <div className="stat-value">{stats ? stats.total_target_persons : '--'}</div>
        <div className="stat-sub">Identidades registradas</div>
      </div>

      <div className="stat-card">
        <div className="stat-header">
          <span className="stat-label">Índice Vectorial FAISS</span>
          <Database className="stat-icon text-purple" size={20} />
        </div>
        <div className="stat-value">{stats ? stats.faiss_indexed_identities.toLocaleString() : '--'}</div>
        <div className="stat-sub">Embeddings 512D activos</div>
      </div>

      <div className="stat-card alert-card-border">
        <div className="stat-header">
          <span className="stat-label">Alertas Pendientes</span>
          <AlertTriangle className="stat-icon text-amber" size={20} />
        </div>
        <div className="stat-value text-amber">{stats ? stats.pending_alerts : '--'}</div>
        <div className="stat-sub">Requieren revisión inmediata</div>
      </div>
    </div>
  );
};
