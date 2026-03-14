import React, { useState } from 'react';
import './FilmStripSidebar.css';
import type { Session } from '../../types';

interface FilmStripSidebarProps {
  sessions: Session[];
  activeSessionId: string | null;
  onSelectSession: (sessionId: string) => void;
  onCreateSession: () => void;
  onDeleteSession?: (sessionId: string) => void;
}

function getSessionTimestamp(session: Session): string | undefined {
  return session.createdAt ?? session.updatedAt;
}

// Format date for display
const formatDate = (dateString?: string): string => {
  if (!dateString) return '—';
  const date = new Date(dateString);
  return date.toLocaleDateString('zh-CN', {
    month: 'short',
    day: 'numeric',
  });
};

// Format time for display
const formatTime = (dateString?: string): string => {
  if (!dateString) return '—';
  const date = new Date(dateString);
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  });
};

// Generate frame number from session id
const getFrameNumber = (sessionId: string): string => {
  // Extract last 4 chars or use index + 1
  const suffix = sessionId.slice(-4).toUpperCase();
  return suffix;
};

// Delete confirmation modal component
interface DeleteConfirmModalProps {
  session: Session;
  onConfirm: () => void;
  onCancel: () => void;
}

const DeleteConfirmModal: React.FC<DeleteConfirmModalProps> = ({
  session,
  onConfirm,
  onCancel,
}) => {
  const frameNumber = getFrameNumber(session.id);

  return (
    <div className="delete-modal-overlay" onClick={onCancel}>
      <div className="delete-modal" onClick={(e) => e.stopPropagation()}>
        <div className="delete-modal__icon">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" />
          </svg>
        </div>
        <h3 className="delete-modal__title">删除 Session？</h3>
        <p className="delete-modal__message">
          这将永久删除 session <strong>#{frameNumber}</strong>，
          此操作无法撤销。
        </p>
        <div className="delete-modal__actions">
          <button className="delete-modal__btn delete-modal__btn--cancel" onClick={onCancel}>
            取消
          </button>
          <button className="delete-modal__btn delete-modal__btn--confirm" onClick={onConfirm}>
            删除
          </button>
        </div>
      </div>
    </div>
  );
};

export const FilmStripSidebar: React.FC<FilmStripSidebarProps> = ({
  sessions,
  activeSessionId,
  onSelectSession,
  onCreateSession,
  onDeleteSession,
}) => {
  const [sessionToDelete, setSessionToDelete] = useState<Session | null>(null);

  const handleDeleteClick = (e: React.MouseEvent, session: Session) => {
    e.stopPropagation();
    setSessionToDelete(session);
  };

  const handleConfirmDelete = () => {
    if (sessionToDelete && onDeleteSession) {
      onDeleteSession(sessionToDelete.id);
    }
    setSessionToDelete(null);
  };

  const handleCancelDelete = () => {
    setSessionToDelete(null);
  };

  return (
    <>
      <aside className="film-strip-sidebar" aria-label="Session list">
        {/* Header */}
        <div className="film-strip__header">
          <h2 className="film-strip__title">Sessions</h2>
          <div className="film-strip__actions">
            <button
              className="film-strip__action-btn"
              onClick={onCreateSession}
              aria-label="Create new session"
              title="New session"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M7 2V12M2 7H12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
              </svg>
            </button>
          </div>
        </div>

        {/* Session List */}
        <div className="film-strip__list" role="list">
          {sessions.length === 0 ? (
            <div className="film-strip__empty">
              <svg
                className="film-strip__empty-icon"
                viewBox="0 0 48 48"
                fill="none"
                xmlns="http://www.w3.org/2000/svg"
              >
                <rect x="8" y="10" width="32" height="28" rx="2" stroke="currentColor" strokeWidth="2"/>
                <path d="M8 18H40" stroke="currentColor" strokeWidth="2"/>
                <path d="M8 30H40" stroke="currentColor" strokeWidth="2"/>
                <circle cx="14" cy="14" r="1.5" fill="currentColor"/>
                <circle cx="34" cy="14" r="1.5" fill="currentColor"/>
                <circle cx="14" cy="34" r="1.5" fill="currentColor"/>
                <circle cx="34" cy="34" r="1.5" fill="currentColor"/>
              </svg>
              <p className="film-strip__empty-title">No sessions yet</p>
              <p className="film-strip__empty-desc">Create a new session to get started</p>
            </div>
          ) : (
            sessions.map((session) => {
              const isActive = session.id === activeSessionId;
              const frameNumber = getFrameNumber(session.id);
              const timestamp = getSessionTimestamp(session);
              const dateLabel = formatDate(timestamp);
              const timeLabel = formatTime(timestamp);

              return (
                <button
                  key={session.id}
                  role="listitem"
                  className={`film-strip-tag ${isActive ? 'film-strip-tag--active' : ''}`}
                  onClick={() => onSelectSession(session.id)}
                  aria-selected={isActive}
                  aria-label={
                    timestamp
                      ? `Session ${frameNumber}, ${dateLabel} ${timeLabel}`
                      : `Session ${frameNumber}, time unknown`
                  }
                >
                  {/* Frame number badge */}
                  <div className="film-strip-tag__frame" aria-hidden="true">
                    {frameNumber}
                  </div>

                  {/* Session info */}
                  <div className="film-strip-tag__label" title={session.id}>
                    {dateLabel}
                  </div>
                  <div className="film-strip-tag__meta">
                    {timeLabel} · runtime
                  </div>

                  {/* Active indicator line */}
                  {isActive && (
                    <div className="film-strip-tag__active-indicator" aria-hidden="true" />
                  )}

                  {/* Delete button */}
                  {onDeleteSession && (
                    <span
                      className="film-strip-tag__delete"
                      onClick={(e) => handleDeleteClick(e, session)}
                      role="button"
                      aria-label={`Delete session ${frameNumber}`}
                      title="Delete session"
                    >
                      <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M18 6L6 18M6 6l12 12" />
                      </svg>
                    </span>
                  )}
                </button>
              );
            })
          )}
        </div>
      </aside>

      {/* Delete confirmation modal */}
      {sessionToDelete && (
        <DeleteConfirmModal
          session={sessionToDelete}
          onConfirm={handleConfirmDelete}
          onCancel={handleCancelDelete}
        />
      )}
    </>
  );
};
