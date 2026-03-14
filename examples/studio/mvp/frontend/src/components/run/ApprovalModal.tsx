import React, { useState, useCallback } from 'react';
import './ApprovalModal.css';
import { Button } from '../ui/Button';
import {
  decideApproval,
  type PendingApproval,
  type ApprovalDecision,
} from '../../lib/approvals';

interface ApprovalModalProps {
  runId: string;
  approvals: PendingApproval[];
  onDecisionSubmitted: () => void;
  onClose?: () => void;
}

export const ApprovalModal: React.FC<ApprovalModalProps> = ({
  runId,
  approvals,
  onDecisionSubmitted,
  onClose,
}) => {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const currentApproval = approvals[currentIndex];
  const hasMultiple = approvals.length > 1;
  const isLast = currentIndex >= approvals.length - 1;

  const handleDecision = useCallback(
    async (decision: ApprovalDecision) => {
      if (!currentApproval || isSubmitting) return;

      setIsSubmitting(true);
      setError(null);

      try {
        await decideApproval(runId, currentApproval.approval_key, decision);

        // After successful submission, move to next or notify parent
        if (isLast) {
          onDecisionSubmitted();
        } else {
          setCurrentIndex((prev) => prev + 1);
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to submit decision';
        setError(message);
      } finally {
        setIsSubmitting(false);
      }
    },
    [currentApproval, isSubmitting, isLast, runId, onDecisionSubmitted]
  );

  const formatJson = (obj: unknown): string => {
    if (obj === null || obj === undefined) return '';
    try {
      return JSON.stringify(obj, null, 2);
    } catch {
      return String(obj ?? '');
    }
  };

  // If no approvals, don't render
  if (!currentApproval) return null;

  return (
    <div className="approval-modal-overlay" onClick={onClose}>
      <div
        className="approval-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="approval-modal-title"
      >
        <div className="approval-modal__header">
          <h3 id="approval-modal-title" className="approval-modal__title">
            {currentApproval.title || 'Approval Required'}
          </h3>
          {hasMultiple && (
            <span className="approval-modal__counter">
              {currentIndex + 1} / {approvals.length}
            </span>
          )}
        </div>

        <div className="approval-modal__body">
          <div className="approval-modal__prompt">
            {currentApproval.prompt || 'The agent is requesting approval to proceed.'}
          </div>

          {currentApproval.metadata && Object.keys(currentApproval.metadata).length > 0 && (
            <div className="approval-modal__section">
              <h4 className="approval-modal__section-title">Metadata</h4>
              <pre className="approval-modal__code">{formatJson(currentApproval.metadata)}</pre>
            </div>
          )}

          {currentApproval.request && typeof currentApproval.request === 'object' && (
            <div className="approval-modal__section">
              <h4 className="approval-modal__section-title">Request Details</h4>
              <pre className="approval-modal__code">{formatJson(currentApproval.request)}</pre>
            </div>
          )}

          {currentApproval.tool && (
            <div className="approval-modal__meta-row">
              <span className="approval-modal__meta-label">Tool:</span>
              <code className="approval-modal__meta-value">{currentApproval.tool}</code>
            </div>
          )}

          {currentApproval.summary && (
            <div className="approval-modal__meta-row">
              <span className="approval-modal__meta-label">Summary:</span>
              <span className="approval-modal__meta-value">{currentApproval.summary}</span>
            </div>
          )}

          {error && (
            <div className="approval-modal__error" role="alert">
              <span className="approval-modal__error-icon">⚠️</span>
              <span className="approval-modal__error-text">{error}</span>
            </div>
          )}
        </div>

        <div className="approval-modal__footer">
          <div className="approval-modal__actions-row">
            <Button
              variant="primary"
              onClick={() => handleDecision('approved')}
              isLoading={isSubmitting}
              disabled={isSubmitting}
            >
              Approve
            </Button>
            <Button
              variant="secondary"
              onClick={() => handleDecision('approved_for_session')}
              isLoading={isSubmitting}
              disabled={isSubmitting}
            >
              Approve for Session
            </Button>
          </div>
          <div className="approval-modal__actions-row">
            <Button
              variant="ghost"
              onClick={() => handleDecision('denied')}
              isLoading={isSubmitting}
              disabled={isSubmitting}
            >
              Deny
            </Button>
            <Button
              variant="danger"
              onClick={() => handleDecision('abort')}
              isLoading={isSubmitting}
              disabled={isSubmitting}
            >
              Abort Run
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
};
