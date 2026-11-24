import React, { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowDown, Menu, RefreshCw } from 'lucide-react';
import {
  createSession,
  fetchOpenClarification,
  fetchSessionMessages,
  fetchSessions,
  getOrCreateGuestUserId,
  respondToClarification,
  sendMessage,
  submitFeedback,
  updateSession,
} from '../api/client';
import ChatSidebar from '../components/ChatSidebar';
import ChatMessage from '../components/ChatMessage';
import ChatInput from '../components/ChatInput';
import ClarificationPrompt from '../components/ClarificationPrompt';
import EmptyState from '../components/EmptyState';

export default function ChatPage() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // 0. Synchronous initialization of guest user and sessions cache
  const SESSIONS_CACHE_KEY = 'tamilheritage_cached_sessions';

  const [userId, setUserId] = useState(() => {
    try {
      return localStorage.getItem('tamilheritage_guest_user_id') || null;
    } catch {
      return null;
    }
  });

  const [inputText, setInputText] = useState('');
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [sendError, setSendError] = useState(null);
  const [lastFailedText, setLastFailedText] = useState(null);
  const [optimisticUserMsg, setOptimisticUserMsg] = useState(null);
  const [showScrollBottom, setShowScrollBottom] = useState(false);

  // Clarification state
  const [activeClarification, setActiveClarification] = useState(null);
  // {clarification_id, entity_name, candidates:[{option_id, label, locality, district, site_id}]}
  const [clarificationBusy, setClarificationBusy] = useState(false);
  const [clarificationStepLabel, setClarificationStepLabel] = useState('');

  const messagesEndRef = useRef(null);
  const scrollContainerRef = useRef(null);

  // 1. Initialize guest user on mount if not in localStorage
  useEffect(() => {
    async function initUser() {
      try {
        const uid = await getOrCreateGuestUserId();
        setUserId(uid);
      } catch (err) {
        console.error('Failed to init guest user:', err);
      }
    }
    if (!userId) {
      initUser();
    }
  }, [userId]);

  // 2. Fetch Sessions for sidebar with instantaneous initialData from localStorage
  const {
    data: sessions = [],
    isLoading: sessionsLoading,
    refetch: refetchSessions,
  } = useQuery({
    queryKey: ['sessions', userId],
    queryFn: async () => {
      const data = await fetchSessions(userId);
      try {
        localStorage.setItem(SESSIONS_CACHE_KEY, JSON.stringify(data));
      } catch (e) {
        // ignore quota error
      }
      return data;
    },
    initialData: () => {
      try {
        const cached = localStorage.getItem(SESSIONS_CACHE_KEY);
        if (cached) {
          const parsed = JSON.parse(cached);
          if (Array.isArray(parsed) && parsed.length > 0) {
            return parsed;
          }
        }
      } catch {
        // ignore
      }
      return undefined;
    },
    enabled: !!userId,
    staleTime: 1000 * 30, // 30s fresh cache
  });

  // 3. Fetch Messages for active session
  const {
    data: fetchedMessages = [],
    isLoading: messagesLoading,
    refetch: refetchMessages,
  } = useQuery({
    queryKey: ['messages', sessionId],
    queryFn: () => fetchSessionMessages(sessionId),
    enabled: !!sessionId,
  });

  // Combine fetched messages with optimistic message
  const messages = [...fetchedMessages];
  if (optimisticUserMsg) {
    messages.push(optimisticUserMsg);
  }

  // Scroll to bottom logic
  const scrollToBottom = (smooth = true) => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto' });
      setShowScrollBottom(false);
    }
  };

  const handleScroll = () => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setShowScrollBottom(distanceFromBottom > 150);
  };

  useEffect(() => {
    if (!showScrollBottom) {
      scrollToBottom(false);
    }
  }, [messages.length, optimisticUserMsg]);

  // Send message mutation
  const handleSend = async (textToSend) => {
    const content = (textToSend !== undefined ? textToSend : inputText).trim();
    if (!content || isSending) return;

    setInputText('');
    setSendError(null);
    setLastFailedText(null);
    setIsSending(true);
    // Clear any active clarification when user sends a new message
    setActiveClarification(null);

    // Optimistic message
    const tempMsg = {
      message_id: Date.now(),
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    };
    setOptimisticUserMsg(tempMsg);

    try {
      let targetSessionId = sessionId;

      // Lazy session creation if on "/"
      if (!targetSessionId) {
        let activeUserId = userId;
        if (!activeUserId) {
          activeUserId = await getOrCreateGuestUserId();
          setUserId(activeUserId);
        }

        const newSession = await createSession({
          userId: activeUserId,
          title: content.slice(0, 50),
          client: 'web',
        });
        targetSessionId = newSession.session_id;
        queryClient.invalidateQueries({ queryKey: ['sessions', activeUserId] });
      }

      // Send message to API with client idempotency key
      const idempotencyKey = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
      await sendMessage(targetSessionId, content, idempotencyKey);

      // If lazy created, navigate to new URL
      if (!sessionId) {
        navigate(`/c/${targetSessionId}`, { replace: true });
      } else {
        await queryClient.invalidateQueries({ queryKey: ['messages', targetSessionId] });
        queryClient.invalidateQueries({ queryKey: ['sessions', userId] });

        // Check if the backend opened a clarification for this session
        try {
          const clar = await fetchOpenClarification(targetSessionId);
          if (clar.status === 'open' && clar.candidates?.length > 0) {
            setActiveClarification({
              clarification_id: clar.clarification_id,
              entity_name: content,
              candidates: clar.candidates,
            });
          }
        } catch (_) {
          // non-fatal
        }
      }
    } catch (err) {
      console.error('Send message failed:', err);
      setSendError(err.message || 'Failed to send message.');
      setLastFailedText(content);
    } finally {
      setIsSending(false);
      setOptimisticUserMsg(null);
    }
  };

  // Chip or free-text clarification handlers
  const handleChipSelect = async (clarificationId, optionId, label) => {
    if (!sessionId) return;
    setClarificationBusy(true);
    setClarificationStepLabel(`Checking sources for ${label}…`);
    // Show the chip label as a user message optimistically
    setOptimisticUserMsg({
      message_id: Date.now(),
      role: 'user',
      content: label,
      created_at: new Date().toISOString(),
    });
    try {
      await respondToClarification({ sessionId, clarificationId, optionId });
      // After backend resolves, send the enriched query as a message
      setActiveClarification(null);
      await sendMessage(sessionId, label);
      await queryClient.invalidateQueries({ queryKey: ['messages', sessionId] });
      queryClient.invalidateQueries({ queryKey: ['sessions', userId] });
    } catch (err) {
      setSendError(err.message || 'Failed to resolve clarification.');
    } finally {
      setClarificationBusy(false);
      setClarificationStepLabel('');
      setOptimisticUserMsg(null);
    }
  };

  const handleClarificationText = async (text) => {
    if (!sessionId || !activeClarification) return;
    setClarificationBusy(true);
    setClarificationStepLabel(`Searching for ${text}…`);
    setOptimisticUserMsg({
      message_id: Date.now(),
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    });
    try {
      await respondToClarification({ sessionId, clarificationId: activeClarification.clarification_id, text });
      setActiveClarification(null);
      await sendMessage(sessionId, text);
      await queryClient.invalidateQueries({ queryKey: ['messages', sessionId] });
      queryClient.invalidateQueries({ queryKey: ['sessions', userId] });
    } catch (err) {
      setSendError(err.message || 'Failed to resolve clarification.');
    } finally {
      setClarificationBusy(false);
      setClarificationStepLabel('');
      setOptimisticUserMsg(null);
    }
  };

  // Session management handlers
  const handleNewChat = () => {
    navigate('/');
    setSendError(null);
    setLastFailedText(null);
    setOptimisticUserMsg(null);
  };

  const handleRenameSession = async (sId, newTitle) => {
    try {
      await updateSession(sId, { title: newTitle });
      queryClient.setQueryData(['sessions', userId], (old = []) =>
        old.map((s) => (s.session_id === sId ? { ...s, title: newTitle } : s))
      );
      queryClient.invalidateQueries({ queryKey: ['sessions', userId] });
    } catch (err) {
      console.error('Rename failed:', err);
    }
  };

  const handleArchiveSession = async (sId) => {
    try {
      await updateSession(sId, { is_archived: true });
      queryClient.setQueryData(['sessions', userId], (old = []) =>
        old.filter((s) => s.session_id !== sId)
      );
      queryClient.invalidateQueries({ queryKey: ['sessions', userId] });
      if (sessionId === sId) {
        navigate('/');
      }
    } catch (err) {
      console.error('Archive failed:', err);
    }
  };

  const handleFeedback = async (messageId, feedbackData) => {
    if (!userId) return;
    try {
      await submitFeedback(messageId, {
        userId,
        ...feedbackData,
      });
    } catch (err) {
      console.error('Feedback failed:', err);
    }
  };

  return (
    <div className="chat-layout">
      {/* Sidebar */}
      <ChatSidebar
        sessions={sessions}
        isLoading={sessionsLoading && sessions.length === 0}
        onNewChat={handleNewChat}
        onRenameSession={handleRenameSession}
        onArchiveSession={handleArchiveSession}
        isOpen={mobileSidebarOpen}
        onClose={() => setMobileSidebarOpen(false)}
      />

      {/* Main chat window */}
      <div className="chat-main-area">
        {/* Mobile Navbar Header */}
        <header className="chat-mobile-header">
          <button
            type="button"
            className="btn-icon mobile-menu-btn"
            onClick={() => setMobileSidebarOpen(true)}
            aria-label="Open chat history menu"
          >
            <Menu size={20} />
          </button>
          <span className="mobile-header-title">TamilHeritage-GPT</span>
          <button
            type="button"
            className="btn-icon mobile-new-btn"
            onClick={handleNewChat}
            aria-label="Start new chat"
          >
            +
          </button>
        </header>

        {/* Messages scrollable container */}
        <main
          ref={scrollContainerRef}
          className="chat-messages-container"
          onScroll={handleScroll}
        >
          <div className="chat-messages-inner">
            {/* Empty state when on "/" with no messages */}
            {!sessionId && messages.length === 0 ? (
              <EmptyState onSelectExample={(query) => handleSend(query)} />
            ) : messagesLoading && messages.length === 0 ? (
              <div className="chat-messages-skeleton-container" aria-label="Loading conversation history">
                <div className="chat-message-row chat-user-row">
                  <div className="chat-skeleton-bubble user shimmer" style={{ width: '40%', height: '38px', marginLeft: 'auto' }} />
                </div>
                <div className="chat-message-row chat-assistant-row">
                  <div className="chat-avatar" aria-hidden="true">🏛️</div>
                  <div className="chat-skeleton-bubble assistant shimmer" style={{ width: '85%', height: '88px' }} />
                </div>
                <div className="chat-message-row chat-user-row">
                  <div className="chat-skeleton-bubble user shimmer" style={{ width: '55%', height: '38px', marginLeft: 'auto' }} />
                </div>
                <div className="chat-message-row chat-assistant-row">
                  <div className="chat-avatar" aria-hidden="true">🏛️</div>
                  <div className="chat-skeleton-bubble assistant shimmer" style={{ width: '75%', height: '110px' }} />
                </div>
              </div>
            ) : (
              <>
                {messages.map((msg, idx) => (
                  <ChatMessage
                    key={msg.message_id || idx}
                    message={msg}
                    onFeedback={handleFeedback}
                  />
                ))}

                {/* Clarification prompt – shown when backend asks user to pick a location */}
                {activeClarification && !isSending && (
                  <div className="chat-message-row chat-assistant-row">
                    <div className="chat-avatar" aria-hidden="true">🏛️</div>
                    <ClarificationPrompt
                      clarificationId={activeClarification.clarification_id}
                      entityName={activeClarification.entity_name}
                      options={activeClarification.candidates}
                      onChipSelect={handleChipSelect}
                      onTextSubmit={handleClarificationText}
                      isBusy={clarificationBusy}
                      stepLabel={clarificationStepLabel}
                    />
                  </div>
                )}

                {/* Typing indicator */}
                {isSending && (
                  <div className="chat-message-row chat-assistant-row" aria-live="polite">
                    <div className="chat-avatar" aria-hidden="true">
                      🏛️
                    </div>
                    <div className="chat-typing-indicator" aria-label="Thinking and consulting heritage records">
                      <span className="typing-dot" />
                      <span className="typing-dot" />
                      <span className="typing-dot" />
                    </div>
                  </div>
                )}

                {/* Error Banner with Retry */}
                {sendError && (
                  <div className="chat-error-banner" role="alert">
                    <span className="error-text">Error: {sendError}</span>
                    {lastFailedText && (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => handleSend(lastFailedText)}
                      >
                        <RefreshCw size={14} />
                        <span>Retry</span>
                      </button>
                    )}
                  </div>
                )}

                {/* Spacer ensuring comfortable gap between upper chat history and input box */}
                <div className="chat-messages-bottom-spacer" />
                <div ref={messagesEndRef} />
              </>
            )}
          </div>
        </main>

        {/* Floating scroll to bottom button */}
        {showScrollBottom && (
          <button
            type="button"
            className="btn-scroll-bottom"
            onClick={() => scrollToBottom(true)}
            aria-label="Scroll to newest message"
          >
            <ArrowDown size={16} />
            <span>New message</span>
          </button>
        )}

        {/* Input box footer */}
        <footer className="chat-footer">
          <ChatInput
            value={inputText}
            onChange={setInputText}
            onSend={() => handleSend()}
            disabled={isSending}
            loading={isSending}
          />
        </footer>
      </div>
    </div>
  );
}
