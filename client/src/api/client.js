/**
 * API client for TamilHeritage-GPT backend.
 * Uses Vite proxy (/api -> http://localhost:8000).
 */

const API_BASE = '/api';

// -------------------------------------------------------------------------
// Guest User & Persistence
// -------------------------------------------------------------------------
const GUEST_USER_KEY = 'tamilheritage_guest_user_id';

export async function createGuestUser() {
  const res = await fetch(`${API_BASE}/users/guest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw new Error(`Failed to create guest user (HTTP ${res.status})`);
  return res.json();
}

export async function getOrCreateGuestUserId() {
  try {
    const cachedId = localStorage.getItem(GUEST_USER_KEY);
    if (cachedId && cachedId.trim()) {
      return cachedId;
    }
  } catch (e) {
    console.warn('localStorage read error:', e);
  }

  const data = await createGuestUser();
  const userId = data.user_id;
  try {
    localStorage.setItem(GUEST_USER_KEY, userId);
  } catch (e) {
    console.warn('localStorage write error:', e);
  }
  return userId;
}

// -------------------------------------------------------------------------
// Chat Sessions
// -------------------------------------------------------------------------
export async function fetchSessions(userId) {
  const params = new URLSearchParams();
  if (userId) params.append('user_id', userId);
  params.append('include_archived', 'false');

  const res = await fetch(`${API_BASE}/chat/sessions?${params.toString()}`);
  if (!res.ok) throw new Error(`Failed to fetch sessions (HTTP ${res.status})`);
  return res.json();
}

export async function createSession({ userId, title = 'New Chat', client = 'web' }) {
  const res = await fetch(`${API_BASE}/chat/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, title, client }),
  });
  if (!res.ok) throw new Error(`Failed to create session (HTTP ${res.status})`);
  return res.json();
}

export async function updateSession(sessionId, { title, is_archived }) {
  const body = {};
  if (title !== undefined) body.title = title;
  if (is_archived !== undefined) body.is_archived = is_archived;

  const res = await fetch(`${API_BASE}/chat/sessions/${sessionId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Failed to update session (HTTP ${res.status})`);
  return res.json();
}

// -------------------------------------------------------------------------
// Messages & History
// -------------------------------------------------------------------------
export async function fetchSessionMessages(sessionId) {
  const res = await fetch(`${API_BASE}/chat/sessions/${sessionId}/messages`);
  if (!res.ok) throw new Error(`Failed to fetch session messages (HTTP ${res.status})`);
  return res.json();
}

export async function sendMessage(sessionId, content, idempotencyKey = null) {
  const headers = { 'Content-Type': 'application/json' };
  if (idempotencyKey) {
    headers['Idempotency-Key'] = idempotencyKey;
  }
  const body = { content };
  if (idempotencyKey) {
    body.idempotency_key = idempotencyKey;
  }

  const res = await fetch(`${API_BASE}/chat/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to send message (HTTP ${res.status})`);
  }
  return res.json();
}

// -------------------------------------------------------------------------
// Feedback
// -------------------------------------------------------------------------
export async function submitFeedback(messageId, { userId, rating, reason, comment }) {
  const res = await fetch(`${API_BASE}/messages/${messageId}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_id: userId,
      rating,
      reason: reason || undefined,
      comment: comment || undefined,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Feedback failed (HTTP ${res.status})`);
  }
  return res.json();
}

// -------------------------------------------------------------------------
// Explorer / Existing APIs
// -------------------------------------------------------------------------
export async function fetchHealth() {
  const res = await fetch(`${API_BASE}/health`);
  if (!res.ok) throw new Error(`Failed to fetch health (HTTP ${res.status})`);
  return res.json();
}

export async function fetchStats() {
  const res = await fetch(`${API_BASE}/stats`);
  if (!res.ok) throw new Error('Failed to fetch stats');
  return res.json();
}

export async function fetchSites({ category, district, status, q, page = 1, page_size = 20 } = {}) {
  const params = new URLSearchParams();
  if (category) params.append('category', category);
  if (district) params.append('district', district);
  if (status) params.append('status', status);
  if (q) params.append('q', q);
  params.append('page', page);
  params.append('page_size', page_size);

  const res = await fetch(`${API_BASE}/sites?${params.toString()}`);
  if (!res.ok) throw new Error('Failed to fetch sites');
  return res.json();
}

export async function fetchSiteDetail(siteId) {
  const res = await fetch(`${API_BASE}/sites/${encodeURIComponent(siteId)}`);
  if (!res.ok) throw new Error(`Failed to fetch site ${siteId}`);
  return res.json();
}

export async function searchChunks({ query, top_k = 5, filters = {} }) {
  const res = await fetch(`${API_BASE}/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, top_k, filters }),
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Search request failed');
  }
  return res.json();
}

export async function reindexChunks() {
  const res = await fetch(`${API_BASE}/admin/reindex`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error('Reindexing request failed');
  return res.json();
}

// -------------------------------------------------------------------------
// Clarification Flow
// -------------------------------------------------------------------------
export async function fetchOpenClarification(sessionId) {
  const res = await fetch(`${API_BASE}/clarifications/open?session_id=${encodeURIComponent(sessionId)}`);
  if (!res.ok) throw new Error(`Failed to fetch clarification (HTTP ${res.status})`);
  return res.json(); // {status, clarification_id, candidates, question_type} or {status:'none'}
}

export async function respondToClarification({ sessionId, clarificationId, optionId, text }) {
  const body = { session_id: sessionId };
  if (optionId) body.option_id = optionId;
  if (text) body.text = text;
  const res = await fetch(`${API_BASE}/clarifications/respond`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Clarification respond failed (HTTP ${res.status})`);
  }
  return res.json();
}
