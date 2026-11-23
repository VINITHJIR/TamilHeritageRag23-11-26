import React, { useState } from 'react';
import { X } from 'lucide-react';

const REASON_OPTIONS = [
  { id: 'wrong_fact', label: 'Wrong fact / Inaccurate' },
  { id: 'wrong_place', label: 'Wrong place / Not this site' },
  { id: 'wrong_language', label: 'Wrong language' },
  { id: 'incomplete', label: 'Incomplete information' },
  { id: 'other', label: 'Other issue' },
];

export default function FeedbackPopover({ messageId, onSubmit, onClose }) {
  const [reason, setReason] = useState('wrong_fact');
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await onSubmit({ rating: -1, reason, comment: comment.trim() || undefined });
      onClose();
    } catch (err) {
      console.error('Failed to submit feedback:', err);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="feedback-popover" role="dialog" aria-labelledby={`feedback-title-${messageId}`}>
      <div className="feedback-popover-header">
        <span id={`feedback-title-${messageId}`} className="feedback-popover-title">
          Provide feedback
        </span>
        <button
          type="button"
          className="btn-icon"
          onClick={onClose}
          aria-label="Close feedback popover"
        >
          <X size={16} />
        </button>
      </div>

      <form onSubmit={handleSubmit}>
        <div className="feedback-reasons-list">
          {REASON_OPTIONS.map((opt) => (
            <label key={opt.id} className="feedback-reason-option">
              <input
                type="radio"
                name={`reason-${messageId}`}
                value={opt.id}
                checked={reason === opt.id}
                onChange={() => setReason(opt.id)}
              />
              <span>{opt.label}</span>
            </label>
          ))}
        </div>

        <textarea
          className="feedback-comment-input"
          placeholder="Optional: What went wrong or what would you expect?"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          rows={2}
          maxLength={500}
        />

        <div className="feedback-actions">
          <button type="button" className="btn btn-secondary btn-sm" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary btn-sm" disabled={submitting}>
            {submitting ? 'Submitting...' : 'Submit'}
          </button>
        </div>
      </form>
    </div>
  );
}
