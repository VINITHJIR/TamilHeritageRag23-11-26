import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { searchChunks } from '../api/client';

export default function Search({ onSelectSite }) {
  const navigate = useNavigate();
  const handleSelect = onSelectSite || ((id) => navigate(`/explorer/sites/${encodeURIComponent(id)}`));

  const [query, setQuery] = useState('');
  const [topK, setTopK] = useState(5);
  const [filters, setFilters] = useState({
    category: '',
    district: '',
    lang: '',
    site_id: '',
  });
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const sampleQueries = [
    { text: 'தஞ்சை பெரிய கோயிலை கட்டியவர் யார்?', desc: 'Tamil: Who built Thanjavur Big Temple?' },
    { text: 'Who built the Big Temple in Thanjavur?', desc: 'English: Builder query' },
    { text: 'ಬನ್ನಾರಿ ಅಮ್ಮನ್ ದೇವಸ್ಥಾನ ಎಲ್ಲಿದೆ?', desc: 'Kannada: Bannari Amman Temple location' },
    { text: 'where can I see Chola bronze statues', cat: 'museum', desc: 'English: Chola bronzes (Filter: museum)' },
  ];

  const handleSearch = async (e) => {
    if (e) e.preventDefault();
    if (!query.trim()) return;

    setLoading(true);
    setError(null);
    try {
      const activeFilters = {};
      if (filters.category) activeFilters.category = filters.category;
      if (filters.district) activeFilters.district = filters.district;
      if (filters.lang) activeFilters.lang = filters.lang;
      if (filters.site_id) activeFilters.site_id = filters.site_id;

      const data = await searchChunks({
        query: query.trim(),
        top_k: Number(topK),
        filters: activeFilters,
      });
      setResults(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const applySample = (q, cat = '') => {
    setQuery(q);
    if (cat) {
      setFilters(prev => ({ ...prev, category: cat }));
    }
  };

  return (
    <div>
      <div style={{ marginBottom: '1.5rem' }}>
        <h1 style={{ fontSize: '1.6rem', color: 'var(--primary)' }}>Semantic Vector Search</h1>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
          Powered by BAAI/bge-m3 dense 1024-dimension embeddings & Qdrant cosine similarity search.
        </p>
      </div>

      {/* Query Formulation Card */}
      <div className="card">
        <form onSubmit={handleSearch}>
          <div style={{ marginBottom: '1rem' }}>
            <label style={{ display: 'block', fontWeight: 600, marginBottom: '0.4rem', fontSize: '0.9rem' }}>
              Search Query (Tamil / English / Kannada / Tanglish):
            </label>
            <input
              type="text"
              placeholder="Enter your question or keywords..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={{ fontSize: '1.05rem', padding: '0.75rem 1rem' }}
            />
          </div>

          {/* Sample quick queries */}
          <div style={{ marginBottom: '1rem' }}>
            <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginRight: '0.5rem' }}>Try Benchmark Queries:</span>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginTop: '0.3rem' }}>
              {sampleQueries.map((sq, idx) => (
                <button
                  key={idx}
                  type="button"
                  className="btn btn-secondary"
                  style={{ fontSize: '0.8rem', padding: '0.3rem 0.6rem' }}
                  onClick={() => applySample(sq.text, sq.cat)}
                >
                  {sq.text}
                </button>
              ))}
            </div>
          </div>

          {/* Optional Filter Controls */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '0.75rem', marginBottom: '1.25rem', paddingTop: '1rem', borderTop: '1px solid var(--border)' }}>
            <div>
              <label style={{ fontSize: '0.8rem', color: 'var(--text-muted)', display: 'block' }}>Category Filter</label>
              <select
                value={filters.category}
                onChange={(e) => setFilters({ ...filters, category: e.target.value })}
              >
                <option value="">All Categories</option>
                <option value="temple">Temple</option>
                <option value="museum">Museum</option>
                <option value="fort">Fort</option>
                <option value="monument">Monument</option>
                <option value="archaeological_site">Archaeological Site</option>
              </select>
            </div>

            <div>
              <label style={{ fontSize: '0.8rem', color: 'var(--text-muted)', display: 'block' }}>District Filter</label>
              <select
                value={filters.district}
                onChange={(e) => setFilters({ ...filters, district: e.target.value })}
              >
                <option value="">All Districts</option>
                <option value="Thanjavur">Thanjavur</option>
                <option value="Chengalpattu">Chengalpattu</option>
                <option value="Madurai">Madurai</option>
                <option value="Erode">Erode</option>
                <option value="Chennai">Chennai</option>
                <option value="Villupuram">Villupuram</option>
                <option value="Sivaganga">Sivaganga</option>
                <option value="Kanniyakumari">Kanniyakumari</option>
              </select>
            </div>

            <div>
              <label style={{ fontSize: '0.8rem', color: 'var(--text-muted)', display: 'block' }}>Language Filter</label>
              <select
                value={filters.lang}
                onChange={(e) => setFilters({ ...filters, lang: e.target.value })}
              >
                <option value="">All Languages</option>
                <option value="ta">Tamil (ta)</option>
                <option value="en">English (en)</option>
                <option value="kn">Kannada (kn)</option>
              </select>
            </div>

            <div>
              <label style={{ fontSize: '0.8rem', color: 'var(--text-muted)', display: 'block' }}>Top-K Results</label>
              <select
                value={topK}
                onChange={(e) => setTopK(e.target.value)}
              >
                <option value="3">Top 3</option>
                <option value="5">Top 5</option>
                <option value="10">Top 10</option>
              </select>
            </div>
          </div>

          <button type="submit" className="btn" disabled={loading} style={{ width: '100%', padding: '0.75rem' }}>
            {loading ? 'Embedding & Searching Qdrant...' : '🔍 Execute Vector Search'}
          </button>
        </form>
      </div>

      {error && (
        <div className="card" style={{ backgroundColor: '#fee2e2', color: '#991b1b', border: '1px solid #f87171' }}>
          <strong>Search Error:</strong> {error}
        </div>
      )}

      {/* Results Section */}
      {results && (
        <div>
          <h2 style={{ fontSize: '1.2rem', marginBottom: '1rem', color: 'var(--primary)' }}>
            Search Results ({results.total_results} items found)
          </h2>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            {results.results.map((item, idx) => (
              <div key={idx} className="card" style={{ marginBottom: 0 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <span style={{ fontSize: '1.1rem', fontWeight: 700, color: 'var(--primary)' }}>
                      #{idx + 1} {item.site_name_en}
                    </span>
                    {item.site_name_ta && (
                      <span className="text-ta" style={{ color: 'var(--text-muted)' }}>
                        ({item.site_name_ta})
                      </span>
                    )}
                    <button
                      className="btn btn-secondary"
                      style={{ fontSize: '0.75rem', padding: '0.2rem 0.5rem' }}
                      onClick={() => handleSelect(item.site_id)}
                    >
                      {item.site_id} →
                    </button>
                  </div>
                  <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                    <span className="badge badge-lang">{item.lang}</span>
                    <span className="badge badge-tag">{item.section}</span>
                    <span
                      style={{
                        fontSize: '0.85rem',
                        fontWeight: 700,
                        padding: '0.2rem 0.5rem',
                        borderRadius: '4px',
                        backgroundColor: item.score > 0.6 ? '#dcfce7' : '#fef9c3',
                        color: item.score > 0.6 ? '#166534' : '#854d0e',
                      }}
                    >
                      Cosine: {item.score.toFixed(4)}
                    </span>
                  </div>
                </div>

                <p
                  className={item.lang === 'ta' ? 'text-ta' : item.lang === 'kn' ? 'text-kn' : ''}
                  style={{ fontSize: '0.95rem', lineHeight: '1.6', marginTop: '0.5rem' }}
                >
                  {item.text}
                </p>
              </div>
            ))}

            {results.results.length === 0 && (
              <div className="card" style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
                No vector search matches found for the query with active filters.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
