import React, { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  Archive,
  Compass,
  Edit2,
  Landmark,
  MessageSquare,
  MoreVertical,
  Plus,
  X,
} from 'lucide-react';

function groupSessionsByDate(sessions = []) {
  const today = [];
  const prev7Days = [];
  const older = [];

  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const sevenDaysAgo = new Date(startOfToday.getTime() - 7 * 24 * 60 * 60 * 1000);

  sessions.forEach((session) => {
    const date = new Date(session.last_message_at || session.started_at);
    if (date >= startOfToday) {
      today.push(session);
    } else if (date >= sevenDaysAgo) {
      prev7Days.push(session);
    } else {
      older.push(session);
    }
  });

  return { today, prev7Days, older };
}

export default function ChatSidebar({
  sessions = [],
  onNewChat,
  onRenameSession,
  onArchiveSession,
  isOpen,
  onClose,
}) {
  const { sessionId: currentSessionId } = useParams();
  const navigate = useNavigate();

  const [menuOpenId, setMenuOpenId] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [editTitle, setEditTitle] = useState('');

  const { today, prev7Days, older } = groupSessionsByDate(sessions);

  const startRename = (s) => {
    setEditingId(s.session_id);
    setEditTitle(s.title || 'New Chat');
    setMenuOpenId(null);
  };

  const handleSaveRename = async (sessionId) => {
    if (editTitle.trim()) {
      await onRenameSession(sessionId, editTitle.trim());
    }
    setEditingId(null);
  };

  const renderSessionItem = (session) => {
    const isSelected = session.session_id === currentSessionId;
    const isEditing = editingId === session.session_id;

    return (
      <div
        key={session.session_id}
        className={`sidebar-session-item ${isSelected ? 'active' : ''}`}
      >
        <MessageSquare size={16} className="session-icon" />

        {isEditing ? (
          <input
            type="text"
            className="sidebar-rename-input"
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            onBlur={() => handleSaveRename(session.session_id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleSaveRename(session.session_id);
              if (e.key === 'Escape') setEditingId(null);
            }}
            autoFocus
          />
        ) : (
          <button
            type="button"
            className="sidebar-session-title"
            onClick={() => {
              navigate(`/c/${session.session_id}`);
              if (onClose) onClose();
            }}
            title={session.title || 'New Chat'}
          >
            {session.title || 'New Chat'}
          </button>
        )}

        {/* Hover action menu */}
        {!isEditing && (
          <div className="sidebar-item-actions">
            <button
              type="button"
              className="sidebar-action-dots"
              onClick={(e) => {
                e.stopPropagation();
                setMenuOpenId(menuOpenId === session.session_id ? null : session.session_id);
              }}
              aria-label="Session options"
            >
              <MoreVertical size={14} />
            </button>

            {menuOpenId === session.session_id && (
              <div className="sidebar-popover-menu">
                <button
                  type="button"
                  className="sidebar-menu-btn"
                  onClick={(e) => {
                    e.stopPropagation();
                    startRename(session);
                  }}
                >
                  <Edit2 size={13} />
                  <span>Rename</span>
                </button>
                <button
                  type="button"
                  className="sidebar-menu-btn"
                  onClick={async (e) => {
                    e.stopPropagation();
                    setMenuOpenId(null);
                    await onArchiveSession(session.session_id);
                  }}
                >
                  <Archive size={13} />
                  <span>Archive</span>
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  const sidebarContent = (
    <aside className="chat-sidebar" aria-label="Chat history">
      {/* Top Header */}
      <div className="sidebar-header">
        <Link to="/" className="sidebar-brand" onClick={onClose}>
          <div className="sidebar-logo">
            <Landmark size={20} />
          </div>
          <div className="sidebar-brand-text">
            <span className="brand-title">TamilHeritage</span>
            <span className="brand-tag">GPT</span>
          </div>
        </Link>

        {onClose && (
          <button
            type="button"
            className="btn-icon mobile-close-btn"
            onClick={onClose}
            aria-label="Close sidebar"
          >
            <X size={18} />
          </button>
        )}
      </div>

      {/* New Chat Button */}
      <div className="sidebar-new-chat-container">
        <button
          type="button"
          className="btn-new-chat"
          onClick={() => {
            onNewChat();
            if (onClose) onClose();
          }}
        >
          <Plus size={16} />
          <span>New chat</span>
        </button>
      </div>

      {/* Session Lists grouped */}
      <div className="sidebar-sessions-list">
        {today.length > 0 && (
          <div className="sidebar-group">
            <div className="sidebar-group-title">Today</div>
            {today.map(renderSessionItem)}
          </div>
        )}

        {prev7Days.length > 0 && (
          <div className="sidebar-group">
            <div className="sidebar-group-title">Previous 7 days</div>
            {prev7Days.map(renderSessionItem)}
          </div>
        )}

        {older.length > 0 && (
          <div className="sidebar-group">
            <div className="sidebar-group-title">Older</div>
            {older.map(renderSessionItem)}
          </div>
        )}

        {sessions.length === 0 && (
          <div className="sidebar-empty-hint">No chats yet</div>
        )}
      </div>

      {/* Footer Link to Explorer */}
      <div className="sidebar-footer">
        <Link
          to="/explorer/dashboard"
          className="sidebar-footer-link"
          onClick={onClose}
        >
          <Compass size={18} />
          <div className="footer-link-text">
            <span>Explorer</span>
            <small>Database & Vectors</small>
          </div>
        </Link>
      </div>
    </aside>
  );

  return (
    <>
      {/* Mobile backdrop */}
      <div
        className={`sidebar-mobile-backdrop ${isOpen ? 'open' : ''}`}
        onClick={onClose}
        aria-hidden="true"
      />
      <div className={`sidebar-container ${isOpen ? 'open' : ''}`}>
        {sidebarContent}
      </div>
    </>
  );
}
