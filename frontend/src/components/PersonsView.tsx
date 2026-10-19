import React, { useState } from 'react';
import type { TargetPerson } from '../types';
import { UserPlus, Trash2, IdCard, CheckCircle2, User } from 'lucide-react';
import { api } from '../services/api';

interface Props {
  persons: TargetPerson[];
  onRefresh: () => void;
}

export const PersonsView: React.FC<Props> = ({ persons, onRefresh }) => {
  const [showModal, setShowModal] = useState(false);
  const [fullName, setFullName] = useState('');
  const [documentId, setDocumentId] = useState('');
  const [notes, setNotes] = useState('');
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      setPhotoFile(file);
      setPreviewUrl(URL.createObjectURL(file));
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!fullName) return;

    try {
      setIsSubmitting(true);
      const fd = new FormData();
      fd.append('full_name', fullName);
      if (documentId) fd.append('document_id', documentId);
      if (notes) fd.append('notes', notes);
      if (photoFile) fd.append('photo', photoFile);

      await api.createPerson(fd);
      setShowModal(false);
      setFullName('');
      setDocumentId('');
      setNotes('');
      setPhotoFile(null);
      setPreviewUrl(null);
      onRefresh();
    } catch (err) {
      alert('Error al registrar persona: ' + err);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDelete = async (pid: number) => {
    if (!confirm('¿Eliminar esta persona e indexación en FAISS?')) return;
    try {
      await api.deletePerson(pid);
      onRefresh();
    } catch (err) {
      alert('Error al eliminar: ' + err);
    }
  };

  return (
    <div className="persons-view-container">
      <div className="section-header">
        <div>
          <h2>Personas Buscadas / Base Biométrica</h2>
          <p className="text-muted-small">Identidades indexadas con vectores ArcFace 512D en FAISS</p>
        </div>
        <button className="primary-btn" onClick={() => setShowModal(true)}>
          <UserPlus size={18} />
          <span>Registrar Persona</span>
        </button>
      </div>

      <div className="persons-grid">
        {persons.map((person) => (
          <div key={person.id} className="person-card">
            <div className="person-photo-container">
              {person.photo_url ? (
                <img
                  src={`http://localhost:8000${person.photo_url}`}
                  alt={person.full_name}
                  className="person-photo"
                />
              ) : (
                <div className="person-photo-placeholder">
                  <User size={48} className="text-muted" />
                </div>
              )}
              <span className="faiss-id-badge">FAISS ID #{person.faiss_id}</span>
            </div>

            <div className="person-card-body">
              <h3 className="person-name">{person.full_name}</h3>
              {person.document_id && (
                <div className="person-meta-row">
                  <IdCard size={14} className="text-muted" />
                  <span>Doc: {person.document_id}</span>
                </div>
              )}
              {person.notes && (
                <p className="person-notes">{person.notes}</p>
              )}
            </div>

            <div className="person-card-footer">
              <span className="indexed-badge">
                <CheckCircle2 size={14} className="text-cyan" />
                <span>Vector Indexado</span>
              </span>
              <button
                className="btn-icon-danger"
                onClick={() => handleDelete(person.id)}
                title="Eliminar de FAISS y Base de Datos"
              >
                <Trash2 size={16} />
              </button>
            </div>
          </div>
        ))}
      </div>

      {showModal && (
        <div className="modal-backdrop">
          <div className="modal-card">
            <div className="modal-header">
              <h3>Registrar Nueva Persona Objetivo</h3>
              <button className="close-btn" onClick={() => setShowModal(false)}>×</button>
            </div>
            <form onSubmit={handleCreate}>
              <div className="form-group">
                <label>Nombre Completo</label>
                <input
                  type="text"
                  required
                  placeholder="ej. Juan Carlos Pérez"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label>Documento de Identidad / Cédula</label>
                <input
                  type="text"
                  placeholder="ej. 12.345.678"
                  value={documentId}
                  onChange={(e) => setDocumentId(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label>Fotografía Facial (Extracción ArcFace automática)</label>
                <input
                  type="file"
                  accept="image/*"
                  onChange={handleFileChange}
                />
                {previewUrl && (
                  <div className="photo-preview mt-2">
                    <img src={previewUrl} alt="Vista previa" className="preview-img" />
                    <span className="text-muted-small">Rostro a procesar para el vector 512D</span>
                  </div>
                )}
              </div>

              <div className="form-group">
                <label>Notas / Motivo de Búsqueda</label>
                <textarea
                  rows={3}
                  placeholder="ej. Persona extraviada el 15/09/2026. Vestimenta reportada..."
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                />
              </div>

              <div className="modal-actions">
                <button type="button" className="secondary-btn" onClick={() => setShowModal(false)}>
                  Cancelar
                </button>
                <button type="submit" className="primary-btn" disabled={isSubmitting}>
                  {isSubmitting ? 'Indexando...' : 'Guardar e Indexar'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
