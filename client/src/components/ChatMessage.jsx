import React, { useState } from 'react';
import { Check, ChevronDown, ChevronRight, Copy, Landmark, ThumbsDown, ThumbsUp } from 'lucide-react';
import SourceCard from './SourceCard';
import FeedbackPopover from './FeedbackPopover';

// Safe markdown renderer supporting bold, italic, code, bullet lists, and paragraphs
function renderSafeMarkdown(content) {
  if (!content) return null;

  // Split lines
  const lines = content.split('\n');
  const elements = [];
  let currentList = [];

  const flushList = () => {
    if (currentList.length > 0) {
      elements.push(
        <ul key={`ul-${elements.length}`} className="chat-markdown-list">
          {currentList.map((item, idx) => (
            <li key={idx}>{parseInlineFormatting(item)}</li>
          ))}
        </ul>
      );
      currentList = [];
    }
  };

  lines.forEach((line, lineIdx) => {
    const trimmed = line.trim();
    const unconfMatch = trimmed.match(/^(?:Unconfirmed \(1 source\)|உறுதிப்படுத்தப்படாதது \(1 ஆதாரம்\)|Unconfirmed \(1 source\)):\s*(.*)$/i);

    if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
      currentList.push(trimmed.slice(2));
    } else if (/^\d+\.\s/.test(trimmed)) {
      currentList.push(trimmed.replace(/^\d+\.\s/, ''));
    } else {
      flushList();
      if (trimmed === '') {
        // Empty spacer
        elements.push(<div key={`sp-${lineIdx}`} className="chat-markdown-gap" />);
      } else if (unconfMatch) {
        elements.push(
          <p key={`p-${lineIdx}`} className="chat-markdown-p">
            <span className="badge-unconfirmed">1 source</span>
            {parseInlineFormatting(unconfMatch[1])}
          </p>
        );
      } else {
        elements.push(
          <p key={`p-${lineIdx}`} className="chat-markdown-p">
            {parseInlineFormatting(trimmed)}
          </p>
        );
      }
    }
  });

  flushList();
  return elements;
}

function parseInlineFormatting(text) {
  // Token parser for **bold**, *italic*, `code`, and [markdown links](url) / raw URLs
  const parts = [];
  const regex = /(\*\*.*?\*\*|\*.*?\*|`.*?`|\[(.*?)\]\((https?:\/\/.*?)\)|https?:\/\/[^\s\)<]+)/g;
  let lastIndex = 0;
  let match;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.substring(lastIndex, match.index));
    }
    const token = match[0];
    if (token.startsWith('**') && token.endsWith('**')) {
      parts.push(<strong key={match.index}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('*') && token.endsWith('*')) {
      parts.push(<em key={match.index}>{token.slice(1, -1)}</em>);
    } else if (token.startsWith('`') && token.endsWith('`')) {
      parts.push(<code key={match.index} className="chat-inline-code">{token.slice(1, -1)}</code>);
    } else if (match[2] && match[3]) {
      // [label](url)
      parts.push(
        <a key={match.index} href={match[3]} target="_blank" rel="noopener noreferrer" className="chat-link">
          {match[2]}
        </a>
      );
    } else if (token.startsWith('http://') || token.startsWith('https://')) {
      parts.push(
        <a key={match.index} href={token} target="_blank" rel="noopener noreferrer" className="chat-link">
          {token}
        </a>
      );
    }
    lastIndex = regex.lastIndex;
  }

  if (lastIndex < text.length) {
    parts.push(text.substring(lastIndex));
  }

  return parts.length > 0 ? parts : text;
}

export default function ChatMessage({ message, onFeedback }) {
  const isUser = message.role === 'user';
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [showFeedbackPopover, setShowFeedbackPopover] = useState(false);
  const [userRating, setUserRating] = useState(null);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.warn('Copy failed:', err);
    }
  };

  const handleThumbsUp = async () => {
    setUserRating(1);
    if (onFeedback) {
      await onFeedback(message.message_id, { rating: 1 });
    }
  };

  const handleThumbsDownClick = () => {
    setShowFeedbackPopover((prev) => !prev);
  };

  const handlePopoverSubmit = async (feedbackData) => {
    setUserRating(-1);
    if (onFeedback) {
      await onFeedback(message.message_id, feedbackData);
    }
  };

  if (isUser) {
    return (
      <div className="chat-message-row chat-user-row">
        <div className="chat-user-bubble">
          <p className="chat-message-text">{message.content}</p>
        </div>
      </div>
    );
  }

  // Assistant message badges
  const langBadges = {
    ta: { label: 'தமிழ்', className: 'badge-lang-ta' },
    en: { label: 'English', className: 'badge-lang-en' },
    'ta-Latn': { label: 'Tanglish', className: 'badge-lang-tanglish' },
    kn: { label: 'ಕನ್ನಡ', className: 'badge-lang-kn' },
  };

  const pathBadges = {
    db: { label: 'From records', className: 'badge-path-db' },
    rejected: { label: 'No reliable info', className: 'badge-path-rejected' },
    web_pending: { label: 'Checking sources…', className: 'badge-path-pending' },
    web_consensus: { label: 'Verified from web', className: 'badge-path-web' },
    web_provisional: { label: 'Provisional', className: 'badge-path-provisional' },
    needs_choice: { label: 'Choice needed', className: 'badge-path-choice' },
    needs_clarification: { label: 'Clarification', className: 'badge-path-clarification' },
    greeting: { label: 'Assistant', className: 'badge-path-greeting' },
  };

  const langInfo = langBadges[message.lang] || { label: message.lang || 'English', className: 'badge-lang-en' };
  const pathInfo = pathBadges[message.path] || pathBadges.db;
  const citations = message.citations || [];

  return (
    <div className="chat-message-row chat-assistant-row" aria-live="polite">
      <div className="chat-avatar" aria-hidden="true" title="TamilHeritage-GPT">
        <Landmark size={20} />
      </div>

      <div className="chat-assistant-content">
        {/* Badges row */}
        <div className="chat-badges-row">
          <span className={`badge ${langInfo.className}`}>{langInfo.label}</span>
          <span className={`badge ${pathInfo.className}`}>{pathInfo.label}</span>
        </div>

        {/* Answer text */}
        <div className="chat-assistant-body">
          {renderSafeMarkdown(message.content)}
        </div>

        {/* Sources accordion */}
        {citations.length > 0 && (
          <div className="chat-sources-accordion">
            <button
              type="button"
              className="chat-sources-toggle"
              onClick={() => setSourcesOpen(!sourcesOpen)}
              aria-expanded={sourcesOpen}
              aria-label={`Toggle sources, ${citations.length} available`}
            >
              <span className="chat-sources-toggle-label">
                {sourcesOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                Sources ({citations.length})
              </span>
            </button>

            {sourcesOpen && (
              <div className="chat-sources-grid">
                {citations.map((c, i) => (
                  <SourceCard key={c.chunk_id || i} citation={c} />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Actions row: Copy, thumbs up, thumbs down, latency */}
        <div className="chat-actions-row">
          <button
            type="button"
            className="chat-action-btn"
            onClick={handleCopy}
            title={copied ? 'Copied!' : 'Copy response'}
            aria-label="Copy response text"
          >
            {copied ? <Check size={14} className="icon-success" /> : <Copy size={14} />}
            <span className="action-label">{copied ? 'Copied' : 'Copy'}</span>
          </button>

          <button
            type="button"
            className={`chat-action-btn ${userRating === 1 ? 'action-active-up' : ''}`}
            onClick={handleThumbsUp}
            title="Helpful response"
            aria-label="Mark helpful"
          >
            <ThumbsUp size={14} />
          </button>

          <button
            type="button"
            className={`chat-action-btn ${userRating === -1 ? 'action-active-down' : ''}`}
            onClick={handleThumbsDownClick}
            title="Report inaccurate or unhelpful"
            aria-label="Mark unhelpful"
            aria-expanded={showFeedbackPopover}
          >
            <ThumbsDown size={14} />
          </button>

          {message.latency_ms && (
            <span className="chat-latency-hint" title={`Latency: ${message.latency_ms} ms`}>
              {message.latency_ms}ms
            </span>
          )}
        </div>

        {/* Feedback Popover */}
        {showFeedbackPopover && (
          <FeedbackPopover
            messageId={message.message_id}
            onSubmit={handlePopoverSubmit}
            onClose={() => setShowFeedbackPopover(false)}
          />
        )}
      </div>
    </div>
  );
}
