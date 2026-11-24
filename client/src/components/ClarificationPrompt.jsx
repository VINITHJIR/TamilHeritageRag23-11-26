/**
 * ClarificationPrompt – shown when the backend returns a needs_clarification
 * response. Renders a question, a set of location chips, and a free-text
 * fallback input so the user can pick or type a location.
 *
 * Props:
 *   clarificationId  – UUID of the clarification row
 *   entityName       – e.g. "Ponniamman Temple"
 *   options          – [{option_id, label, locality, district, site_id}]
 *   onChipSelect     – called with (clarificationId, option_id, label)
 *   onTextSubmit     – called with (text)
 *   isBusy           – show stepper instead of chips while resolving
 *   stepLabel        – step label to show in the stepper
 */

import React, { useRef, useState } from 'react';
import { Check, Loader2, MapPin } from 'lucide-react';

export default function ClarificationPrompt({
  clarificationId,
  entityName,
  options = [],
  onChipSelect,
  onTextSubmit,
  isBusy = false,
  stepLabel = '',
}) {
  const [localText, setLocalText] = useState('');
  const inputRef = useRef(null);

  const handleChip = (opt) => {
    if (isBusy) return;
    onChipSelect(clarificationId, opt.option_id, opt.label || `${opt.locality} · ${opt.district}`);
  };

  const handleSubmitText = () => {
    const val = localText.trim();
    if (!val || isBusy) return;
    onTextSubmit(val);
    setLocalText('');
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleSubmitText();
    }
  };

  return (
    <div className="clarification-prompt" role="region" aria-label="Clarification options">
      {/* Stepper — shown while resolving or discovering */}
      {isBusy && (
        <div className="clarification-stepper" aria-live="polite">
          <Loader2 size={16} className="clarification-stepper-icon spinning" />
          <span className="clarification-stepper-label">{stepLabel || 'Checking sources…'}</span>
        </div>
      )}

      {/* Question bubble */}
      {!isBusy && (
        <>
          <p className="clarification-question">
            <MapPin size={15} className="clarification-question-icon" />
            Tamil Nadu has several <strong>{entityName}</strong>. Which one do you mean?
          </p>

          {/* Location chips */}
          <div className="clarification-chips" role="group" aria-label="Location options">
            {options.map((opt) => {
              const label = opt.label || `${opt.locality} · ${opt.district}`;
              const isVerified = !!opt.site_id;
              return (
                <button
                  key={opt.option_id}
                  type="button"
                  className={`chip${isVerified ? ' chip--verified' : ''}`}
                  onClick={() => handleChip(opt)}
                  aria-label={`Select ${label}${isVerified ? ' (already in our records)' : ''}`}
                >
                  {label}
                  {isVerified && (
                    <span className="chip-badge" title="Already in our heritage records">
                      <Check size={11} />
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {/* Free-text fallback */}
          <div className="clarification-freetext">
            <span className="clarification-hint">or type a location:</span>
            <div className="clarification-freetext-row">
              <input
                ref={inputRef}
                type="text"
                className="clarification-input"
                placeholder="e.g. Egmore, Tiruvarur…"
                value={localText}
                onChange={(e) => setLocalText(e.target.value)}
                onKeyDown={handleKeyDown}
                aria-label="Type a location to narrow down"
              />
              <button
                type="button"
                className="btn btn-primary btn-sm clarification-submit"
                onClick={handleSubmitText}
                disabled={!localText.trim()}
                aria-label="Submit location"
              >
                Go
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
