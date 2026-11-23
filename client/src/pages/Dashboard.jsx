import React, { useEffect, useState } from 'react';
import { fetchHealth, fetchStats, reindexChunks } from '../api/client';

export default function Dashboard() {
  const [health, setHealth] = useState(null);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [reindexing, setReindexing] = useState(false);
  const [msg, setMsg] = useState(null);

  const loadData = async () => {
    setLoading(true);
    // allSettled: a failing health call must not hide stats (and vice versa).
    const [h, s] = await Promise.allSettled([fetchHealth(), fetchStats()]);
    if (h.status === 'fulfilled') setHealth(h.value);
    if (s.status === 'fulfilled') setStats(s.value);
    const errors = [h, s].filter((r) => r.status === 'rejected').map((r) => r.reason?.message || String(r.reason));
    setMsg(errors.length ? { type: 'error', text: errors.join(' · ') } : null);
    setLoading(false);
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleReindex = async () => {
    if (!confirm('Reindex all 30 text chunks into Qdrant & chunk_registry?')) return;
    setReindexing(true);
    try {
      const res = await reindexChunks();
      setMsg({ type: 'success', text: res.message });
      await loadData();
    } catch (err) {
      setMsg({ type: 'error', text: err.message });
    } finally {
      setReindexing(false);
    }
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
        <div>
          <h1 style={{ fontSize: '1.6rem', color: 'var(--primary)' }}>System Dashboard & Health</h1>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
            Level 1 status: PostgreSQL System of Record + Qdrant Vector Index
          </p>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem' }}>
          <button className="btn btn-secondary" onClick={loadData} disabled={loading}>
            {loading ? 'Refreshing...' : 'Refresh Status'}
          </button>
          <button className="btn" onClick={handleReindex} disabled={reindexing}>
            {reindexing ? 'Reindexing...' : '⚡ Reindex Chunks'}
          </button>
        </div>
      </div>

      {msg && (
        <div style={{
          padding: '0.8rem 1rem',
          borderRadius: '6px',
          marginBottom: '1.5rem',
          backgroundColor: msg.type === 'success' ? '#dcfce7' : '#fee2e2',
          color: msg.type === 'success' ? '#166534' : '#991b1b',
          fontSize: '0.9rem'
        }}>
          {msg.text}
        </div>
      )}

      {/* Health Indicator Cards */}
      <div className="grid-cols-3">
        <div className="card" style={{ marginBottom: 0 }}>
          <div className="card-title">PostgreSQL Database</div>
          <div style={{ display: 'flex', alignItems: 'center', fontSize: '1.1rem', fontWeight: 600 }}>
            <span className={`status-indicator ${health?.postgres === 'ok' ? 'status-ok' : 'status-err'}`}></span>
            {health?.postgres === 'ok' ? 'Connected (Heritage_DBJournal)' : 'Disconnected / Error'}
          </div>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: '0.5rem' }}>
            Port 5432 • psycopg 3 driver • pg_trgm enabled
          </p>
        </div>

        <div className="card" style={{ marginBottom: 0 }}>
          <div className="card-title">Qdrant Vector Database</div>
          <div style={{ display: 'flex', alignItems: 'center', fontSize: '1.1rem', fontWeight: 600 }}>
            <span className={`status-indicator ${health?.qdrant === 'ok' ? 'status-ok' : 'status-err'}`}></span>
            {health?.qdrant === 'ok' ? 'Operational (Docker Engine)' : 'Unavailable / Error'}
          </div>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: '0.5rem' }}>
            Collection: heritage_chunks • Dense (1024d) + Sparse
          </p>
        </div>

        <div className="card" style={{ marginBottom: 0 }}>
          <div className="card-title">Embedding Model</div>
          <div style={{ display: 'flex', alignItems: 'center', fontSize: '1.1rem', fontWeight: 600 }}>
            <span className={`status-indicator ${health?.embed_model_loaded ? 'status-ok' : 'status-pending'}`}></span>
            {health?.embed_model_loaded ? 'Loaded in Memory' : 'Lazy-loaded on first query'}
          </div>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: '0.5rem' }}>
            Model: BAAI/bge-m3 • Device: auto (CUDA/CPU)
          </p>
        </div>
      </div>

      {/* Core Counts */}
      {stats && (
        <div className="card">
          <div className="card-title">Database & Vector Index Metrics</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '1rem', textAlign: 'center' }}>
            <div style={{ padding: '1rem', background: '#fdfaf6', borderRadius: '6px' }}>
              <div style={{ fontSize: '2rem', fontWeight: 700, color: 'var(--primary)' }}>{stats.sites}</div>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Heritage Sites</div>
            </div>
            <div style={{ padding: '1rem', background: '#fdfaf6', borderRadius: '6px' }}>
              <div style={{ fontSize: '2rem', fontWeight: 700, color: 'var(--primary)' }}>{stats.chunks_registry}</div>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>chunk_registry Rows</div>
            </div>
            <div style={{ padding: '1rem', background: '#fdfaf6', borderRadius: '6px' }}>
              <div style={{ fontSize: '2rem', fontWeight: 700, color: 'var(--accent-green)' }}>{stats.qdrant_points}</div>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Qdrant Points (Dense 1024)</div>
            </div>
            <div style={{ padding: '1rem', background: '#fdfaf6', borderRadius: '6px' }}>
              <div style={{ fontSize: '2rem', fontWeight: 700, color: 'var(--primary)' }}>{stats.users}</div>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Sample Users</div>
            </div>
            <div style={{ padding: '1rem', background: '#fdfaf6', borderRadius: '6px' }}>
              <div style={{ fontSize: '2rem', fontWeight: 700, color: 'var(--primary)' }}>{stats.chat_messages}</div>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Chat Messages</div>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem', marginTop: '1.5rem' }}>
            <div>
              <h4 style={{ fontSize: '0.95rem', marginBottom: '0.75rem', color: 'var(--text-main)' }}>Chunks by Language</h4>
              <table>
                <thead>
                  <tr>
                    <th>Language</th>
                    <th>Code</th>
                    <th>Count</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(stats.chunks_per_lang || {}).map(([lang, count]) => (
                    <tr key={lang}>
                      <td>
                        {lang === 'ta' && 'Tamil (தமிழ்)'}
                        {lang === 'en' && 'English'}
                        {lang === 'kn' && 'Kannada (ಕನ್ನಡ)'}
                        {lang === 'ta-Latn' && 'Tanglish'}
                      </td>
                      <td><span className="badge badge-lang">{lang}</span></td>
                      <td><strong>{count}</strong></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div>
              <h4 style={{ fontSize: '0.95rem', marginBottom: '0.75rem', color: 'var(--text-main)' }}>Sites by Primary Category</h4>
              <table>
                <thead>
                  <tr>
                    <th>Category</th>
                    <th>Count</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(stats.sites_per_primary_category || {}).map(([cat, count]) => (
                    <tr key={cat}>
                      <td><span className="badge badge-tag">{cat}</span></td>
                      <td><strong>{count}</strong></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
