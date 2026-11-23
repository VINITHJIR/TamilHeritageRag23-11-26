import React, { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowDown, Menu, RefreshCw } from 'lucide-react';
import {
  createSession,
  fetchSessionMessages,
  fetchSessions,
  getOrCreateGuestUserId,
  sendMessage,
  submitFeedback,
  updateSession,
} from '../api/client';
import ChatSidebar from '../components/ChatSidebar';
import ChatMessage from '../components/ChatMessage';
import ChatInput from '../components/ChatInput';
import EmptyState from '../components/EmptyState';

export default function ChatPage() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [userId, setUserId] = useState(null);
  const [inputText, setInputText] = useState('');
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [sendError, setSendError] = useState(null);
  const [lastFailedText, setLastFailedText] = useState(null);
  const [optimisticUserMsg, setOptimisticUserMsg] = useState(null);
  const [showScrollBottom, setShowScrollBottom] = useState(false);

  const messagesEndRef = useRef(null);
  const scrollContainerRef = useRef(null);

  // 1. Initialize guest user on mount
  useEffect(() => {
    async function initUser() {
      try {
        const uid = await getOrCreateGuestUserId();
        setUserId(uid);
      } catch (err) {
        console.error('Failed to init guest user:', err);
      }
    }
    initUser();
  }, []);

  // 2. Fetch Sessions for sidebar
  const { data: sessions = [], refetch: refetchSessions } = useQuery({
    queryKey: ['sessions', userId],
    queryFn: () => fetchSessions(userId),
    enabled: !!userId,
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
      queryClient.invalidateQueries({ queryKey: ['sessions', userId] });
    } catch (err) {
      console.error('Rename failed:', err);
    }
  };

  const handleArchiveSession = async (sId) => {
    try {
      await updateSession(sId, { is_archived: true });
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
            ) : (
              <>
                {messages.map((msg, idx) => (
                  <ChatMessage
                    key={msg.message_id || idx}
                    message={msg}
                    onFeedback={handleFeedback}
                  />
                ))}

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
