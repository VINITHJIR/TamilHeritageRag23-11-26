import React, { useEffect, useRef } from 'react';
import { ArrowUp } from 'lucide-react';

const MAX_CHARS = 1000;

export default function ChatInput({ value, onChange, onSend, disabled, loading }) {
  const textareaRef = useRef(null);

  // Auto-grow textarea up to max 6 lines (~160px)
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const newHeight = Math.min(el.scrollHeight, 160);
    el.style.height = `${Math.max(48, newHeight)}px`;
  }, [value]);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!disabled && !loading && value.trim()) {
        onSend();
      }
    }
  };

  const charCount = value.length;
  const showCounter = charCount >= 750;
  const isNearLimit = charCount >= 950;

  return (
    <div className="chat-input-wrapper">
      <div className="chat-input-box">
        <textarea
          ref={textareaRef}
          className="chat-textarea"
          value={value}
          onChange={(e) => onChange(e.target.value.slice(0, MAX_CHARS))}
          onKeyDown={handleKeyDown}
          placeholder="Ask about Tamil Nadu heritage… / தமிழ்நாட்டுப் பாரம்பரியம் பற்றிக் கேளுங்கள்…"
          rows={1}
          disabled={disabled || loading}
          aria-label="Ask about Tamil Nadu heritage"
        />

        <div className="chat-input-actions">
          {showCounter && (
            <span
              className={`chat-char-counter ${isNearLimit ? 'counter-warning' : ''}`}
              aria-live="polite"
            >
              {charCount}/{MAX_CHARS}
            </span>
          )}

          <button
            type="button"
            className="chat-send-btn"
            onClick={onSend}
            disabled={disabled || loading || !value.trim()}
            aria-label="Send message"
            title="Send (Enter)"
          >
            <ArrowUp size={18} />
          </button>
        </div>
      </div>
    </div>
  );
}
