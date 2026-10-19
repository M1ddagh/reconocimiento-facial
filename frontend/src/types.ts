export type OperationalStatus =
  | 'starting'
  | 'running'
  | 'reconnecting'
  | 'stopped'
  | 'offline'
  | 'stalled'
  | 'status_unavailable'
  | 'error';

export interface CameraTelemetry {
  pid?: number | null;
  fps: number;
  frames_processed: number;
  total_frames_read: number;
  last_frame_timestamp: number;
  reconnect_attempts: number;
  events_published: number;
  events_failed: number;
  last_error?: string | null;
  started_at?: number | null;
  updated_at?: number | null;
}

export interface Camera {
  id: number;
  camera_id: string;
  name: string;
  location?: string | null;
  rtsp_url?: string | null;
  is_active: boolean;
  created_at: number;
  operational_status?: OperationalStatus;
  telemetry?: CameraTelemetry | null;
}

export interface CameraUpdatePayload {
  name?: string;
  location?: string | null;
  rtsp_url?: string | null;
  is_active?: boolean;
}

export interface TargetPerson {
  id: number;
  full_name: string;
  document_id?: string;
  notes?: string;
  photo_url?: string;
  faiss_id: number;
  is_active: boolean;
  created_at: number;
}

export interface AlertEvent {
  id?: number;
  alert_id: string;
  camera_id: string;
  matched_subject_id: number;
  subject_name?: string;
  similarity: number;
  bbox?: string;
  crop_url?: string;
  crop_image_base64?: string;
  status: 'pending' | 'reviewed' | 'confirmed' | 'dismissed';
  timestamp: number;
}

export interface SystemStats {
  total_cameras: number;
  active_cameras: number;
  total_target_persons: number;
  total_alerts: number;
  pending_alerts: number;
  faiss_indexed_identities: number;
}
