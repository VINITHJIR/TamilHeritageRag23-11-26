/**
 * API client for TamilHeritage-GPT backend.
 * Uses Vite proxy (/api -> http://localhost:8000).
 */

const API_BASE = '/api';

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
