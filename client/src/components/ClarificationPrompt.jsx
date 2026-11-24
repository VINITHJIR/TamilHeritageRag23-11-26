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
  kind = 'identity',
  field = null,
}) {
  const [localText, setLocalText] = useState('');
  const inputRef = useRef(null);

  const handleChip = (opt) => {
    if (isBusy) return;
    const optId = opt.choice_id || opt.option_id;
    const label = opt.value || opt.label || `${opt.locality} · ${opt.district}`;
    onChipSelect(clarificationId, optId, label);
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

  const isValueChoice = kind === 'value';

  return (
    <div className="clarification-prompt" role="region" aria-label="Clarification options">
      {/* Stepper — shown while resolving or discovering */}
      {isBusy && (
        <div className="clarification-stepper" aria-live="polite">
          <Loader2 size={16} className="clarification-stepper-icon spinning" />
          <span className="clarification-stepper-label">{stepLabel || 'Checking sources…'}</span>
        </div>
      )}

      {/* Question bubble and cards/chips */}
      {!isBusy && (
        <>
          <p className="clarification-question">
            <MapPin size={15} className="clarification-question-icon" />
            {isValueChoice ? (
              <>Sources give different answers for <strong>{field || 'this attribute'}</strong>. Which source's details would you like to see?</>
            ) : (
              <>Tamil Nadu has several <strong>{entityName}</strong>. Which one do you mean?</>
            )}
          </p>

          {/* Value Choice: Cards with source badges & support bar (Rule 4.4) */}
          {isValueChoice ? (
            <div className="choice-cards-grid" role="group" aria-label="Value choices">
              {options.map((opt, idx) => {
                if (opt.value === 'Compare all') {
                  return (
                    <button
                      key={opt.choice_id || idx}
                      type="button"
                      className="btn btn-outline-primary btn-sm"
                      style={{ marginTop: '4px', alignSelf: 'flex-start' }}
                      onClick={() => handleChip(opt)}
                    >
                      Compare all accounts
                    </button>
                  );
                }
                const ratioPct = opt.support_ratio ? Math.round(opt.support_ratio * 100) : null;
                return (
                  <div
                    key={opt.choice_id || idx}
                    className="choice-card"
                    role="button"
                    tabIndex={0}
                    onClick={() => handleChip(opt)}
                    onKeyDown={(e) => { if (e.key === 'Enter') handleChip(opt); }}
                  >
                    <div className="choice-card-header">
                      <span>{opt.value}</span>
                      {ratioPct !== null && (
                        <span className="support-bar-label">{ratioPct}%</span>
                      )}
                    </div>

                    {ratioPct !== null && (
                      <div className="support-bar">
                        <div className="support-bar-track">
                          <div className="support-bar-fill" style={{ width: `${ratioPct}%` }} />
                        </div>
                      </div>
                    )}

                    {opt.sources && opt.sources.length > 0 && (
                      <div className="choice-card-sources">
                        {opt.sources.map((s, sIdx) => {
                          const tier = (s.tier || 'C').toUpperCase();
                          return (
                            <span key={sIdx} className={`badge-tier tier-${tier.toLowerCase()}`}>
                              {s.domain || 'Source'} (Tier {tier})
                            </span>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            /* Location chips */
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
          )}

          {/* Free-text fallback */}
          {!isValueChoice && (
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
          )}
        </>
      )}
    </div>
  );
}
