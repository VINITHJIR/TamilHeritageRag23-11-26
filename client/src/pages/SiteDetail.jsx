import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { fetchSiteDetail } from '../api/client';

export default function SiteDetail({ siteId, onBack }) {
  const params = useParams();
  const navigate = useNavigate();
  const effectiveSiteId = siteId || params.siteId;
  const handleBack = onBack || (() => navigate('/explorer/sites'));

  const [site, setSite] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    async function load() {
      if (!effectiveSiteId) return;
      setLoading(true);
      try {
        const data = await fetchSiteDetail(effectiveSiteId);
        setSite(data);
        setError(null);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [effectiveSiteId]);

  if (loading) {
    return <div style={{ padding: '2rem', textAlign: 'center' }}>Loading site details...</div>;
  }

  if (error || !site) {
    return (
      <div className="card">
        <div style={{ color: 'var(--accent-red)' }}>Error: {error || 'Site not found'}</div>
        <button className="btn btn-secondary" style={{ marginTop: '1rem' }} onClick={handleBack}>
          ← Back to Sites
        </button>
      </div>
    );
  }

  // Group aliases by language
  const aliasesByLang = {};
  (site.aliases || []).forEach((a) => {
    if (!aliasesByLang[a.lang]) aliasesByLang[a.lang] = [];
    aliasesByLang[a.lang].push(a.alias);
  });

  // Group chunks by section
  const chunksBySection = {};
  (site.chunks || []).forEach((c) => {
    if (!chunksBySection[c.section]) chunksBySection[c.section] = [];
    chunksBySection[c.section].push(c);
  });

  return (
    <div>
      <div style={{ marginBottom: '1rem' }}>
        <button className="btn btn-secondary" onClick={onBack} style={{ padding: '0.4rem 0.8rem', fontSize: '0.85rem' }}>
          ← Back to Sites List
        </button>
      </div>

      {/* Main Header Card */}
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Site ID: <code>{site.site_id}</code></span>
            <h1 style={{ fontSize: '1.8rem', color: 'var(--primary)', marginTop: '0.2rem' }}>{site.name_en}</h1>
            {site.name_ta && (
              <h2 className="text-ta" style={{ fontSize: '1.4rem', color: 'var(--text-main)', marginTop: '0.2rem', fontWeight: 600 }}>
                {site.name_ta}
              </h2>
            )}
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <span className={`badge ${site.status === 'verified' ? 'badge-verified' : 'badge-pending'}`}>
              {site.status}
            </span>
            <span className="badge badge-tag">Trust: {site.trust}</span>
          </div>
        </div>

        {/* Categories */}
        <div style={{ marginTop: '1rem' }}>
          <strong>Categories: </strong>
          {site.categories?.map((cat) => (
            <span key={cat} className="badge badge-tag" style={{ fontSize: '0.85rem' }}>{cat}</span>
          ))}
        </div>

        {/* Location & Metadata */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem', marginTop: '1.25rem', paddingTop: '1.25rem', borderTop: '1px solid var(--border)' }}>
          <div>
            <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>District</span>
            <div style={{ fontWeight: 600 }}>{site.district_name || '—'}</div>
          </div>
          <div>
            <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Subdistrict (Taluk)</span>
            <div style={{ fontWeight: 600 }}>{site.subdistrict_name || '—'}</div>
          </div>
          <div>
            <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Local Body</span>
            <div style={{ fontWeight: 600 }}>{site.local_body_name || '—'} {site.local_body_type ? `(${site.local_body_type})` : ''}</div>
          </div>
          <div>
            <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Coordinates</span>
            <div style={{ fontWeight: 600 }}>
              {site.lat && site.lon ? `${site.lat}, ${site.lon}` : 'Unmapped'}
            </div>
          </div>
          <div>
            <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Period / Dynasty</span>
            <div style={{ fontWeight: 600 }}>{site.period || '—'} {site.dynasty ? `(${site.dynasty})` : ''}</div>
          </div>
        </div>
      </div>

      {/* Aliases Card */}
      <div className="card">
        <div className="card-title">Known Aliases (Multilingual)</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '1rem' }}>
          {Object.entries(aliasesByLang).map(([lang, list]) => (
            <div key={lang} style={{ padding: '0.75rem', background: '#fdfaf6', borderRadius: '6px' }}>
              <span className="badge badge-lang" style={{ marginBottom: '0.5rem' }}>
                {lang === 'ta' && 'Tamil (தமிழ்)'}
                {lang === 'en' && 'English'}
                {lang === 'kn' && 'Kannada (ಕನ್ನಡ)'}
                {lang === 'ta-Latn' && 'Tanglish (ta-Latn)'}
              </span>
              <ul style={{ paddingLeft: '1.2rem', marginTop: '0.3rem', fontSize: '0.9rem' }}>
                {list.map((alias, idx) => (
                  <li key={idx} className={lang === 'ta' ? 'text-ta' : lang === 'kn' ? 'text-kn' : ''}>
                    {alias}
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {site.aliases?.length === 0 && (
            <div style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>No recorded aliases.</div>
          )}
        </div>
      </div>

      {/* Facts Card */}
      {site.facts?.length > 0 && (
        <div className="card">
          <div className="card-title">Verified Field-level Facts</div>
          <table>
            <thead>
              <tr>
                <th>Field</th>
                <th>Value</th>
                <th>Language</th>
                <th>Status</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {site.facts.map((fact, idx) => (
                <tr key={idx}>
                  <td><code>{fact.field}</code></td>
                  <td><strong>{fact.value}</strong></td>
                  <td><span className="badge badge-lang">{fact.lang}</span></td>
                  <td><span className="badge badge-verified">{fact.status}</span></td>
                  <td>
                    {fact.source_url ? (
                      <a href={fact.source_url} target="_blank" rel="noopener noreferrer">Reference ↗</a>
                    ) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Chunks Card */}
      <div className="card">
        <div className="card-title">Indexed Text Chunks (Qdrant & chunk_registry)</div>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginBottom: '1rem' }}>
          Text chunks retrieved from Qdrant vector index payload using chunk UUIDs registered in PostgreSQL.
        </p>

        {Object.entries(chunksBySection).map(([section, chunkList]) => (
          <div key={section} style={{ marginBottom: '1.5rem' }}>
            <h4 style={{ textTransform: 'capitalize', color: 'var(--primary)', marginBottom: '0.5rem' }}>
              📁 Section: {section.replace('_', ' ')}
            </h4>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {chunkList.map((chunk) => (
                <div
                  key={chunk.chunk_id}
                  style={{
                    padding: '1rem',
                    background: '#fdfaf6',
                    border: '1px solid var(--border)',
                    borderRadius: '6px'
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.4rem', fontSize: '0.8rem' }}>
                    <div>
                      <span className="badge badge-lang" style={{ marginRight: '0.5rem' }}>{chunk.lang}</span>
                      <span style={{ color: 'var(--text-muted)' }}>UUID: <code>{chunk.chunk_id}</code></span>
                    </div>
                    {chunk.token_count && (
                      <span style={{ color: 'var(--text-muted)' }}>{chunk.token_count} tokens (bge-m3)</span>
                    )}
                  </div>
                  <p
                    className={chunk.lang === 'ta' ? 'text-ta' : chunk.lang === 'kn' ? 'text-kn' : ''}
                    style={{ fontSize: '0.95rem', lineHeight: '1.6', marginTop: '0.3rem' }}
                  >
                    {chunk.text || <em style={{ color: 'var(--text-muted)' }}>Text payload not yet indexed in Qdrant</em>}
                  </p>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
