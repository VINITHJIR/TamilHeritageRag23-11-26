import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchSites } from '../api/client';

export default function Sites({ onSelectSite }) {
  const navigate = useNavigate();
  const handleSelect = onSelectSite || ((id) => navigate(`/explorer/sites/${encodeURIComponent(id)}`));

  const [sites, setSites] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState({
    q: '',
    category: '',
    district: '',
    status: '',
  });

  const loadSites = async () => {
    setLoading(true);
    try {
      const data = await fetchSites({
        q: filters.q,
        category: filters.category,
        district: filters.district,
        status: filters.status,
      });
      setSites(data.sites || []);
      setTotal(data.total || 0);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSites();
  }, [filters.category, filters.district, filters.status]);

  const handleSearchSubmit = (e) => {
    e.preventDefault();
    loadSites();
  };

  return (
    <div>
      <div style={{ marginBottom: '1.5rem' }}>
        <h1 style={{ fontSize: '1.6rem', color: 'var(--primary)' }}>Tamil Nadu Heritage Sites</h1>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
          Explore curated temples, monuments, forts, and archaeological sites across Tamil Nadu.
        </p>
      </div>

      {/* Filter Bar */}
      <div className="card" style={{ padding: '1rem' }}>
        <form onSubmit={handleSearchSubmit} style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr auto', gap: '0.75rem', alignItems: 'center' }}>
          <div>
            <input
              type="text"
              placeholder="Search name or alias (e.g., Big Temple, தஞ்சை, Shore)..."
              value={filters.q}
              onChange={(e) => setFilters({ ...filters, q: e.target.value })}
            />
          </div>
          <div>
            <select
              value={filters.category}
              onChange={(e) => setFilters({ ...filters, category: e.target.value })}
            >
              <option value="">All Categories</option>
              <option value="temple">Temple</option>
              <option value="monument">Monument</option>
              <option value="museum">Museum</option>
              <option value="fort">Fort</option>
              <option value="palace">Palace</option>
              <option value="archaeological_site">Archaeological Site</option>
              <option value="memorial">Memorial</option>
            </select>
          </div>
          <div>
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
            <select
              value={filters.status}
              onChange={(e) => setFilters({ ...filters, status: e.target.value })}
            >
              <option value="">All Status</option>
              <option value="verified">Verified</option>
              <option value="pending">Pending</option>
            </select>
          </div>
          <div>
            <button type="submit" className="btn" disabled={loading}>
              {loading ? 'Searching...' : 'Search'}
            </button>
          </div>
        </form>
      </div>

      {/* Results Table */}
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
          <div style={{ fontSize: '0.9rem', color: 'var(--text-muted)' }}>
            Showing <strong>{sites.length}</strong> of <strong>{total}</strong> sites
          </div>
        </div>

        <table>
          <thead>
            <tr>
              <th>Site ID</th>
              <th>Name (English)</th>
              <th>Name (Tamil)</th>
              <th>District</th>
              <th>Primary Category</th>
              <th>Status</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {sites.map((site) => (
              <tr key={site.site_id}>
                <td><code>{site.site_id}</code></td>
                <td>
                  <strong>{site.name_en}</strong>
                  {site.categories && (
                    <div style={{ marginTop: '0.2rem' }}>
                      {site.categories.slice(0, 3).map((c) => (
                        <span key={c} className="badge badge-tag">{c}</span>
                      ))}
                    </div>
                  )}
                </td>
                <td className="text-ta" style={{ fontSize: '1rem' }}>{site.name_ta || '—'}</td>
                <td>{site.district_name || '—'}</td>
                <td><span className="badge badge-tag">{site.primary_category}</span></td>
                <td>
                  <span className={`badge ${site.status === 'verified' ? 'badge-verified' : 'badge-pending'}`}>
                    {site.status}
                  </span>
                </td>
                <td>
                  <button
                    className="btn btn-secondary"
                    style={{ padding: '0.3rem 0.7rem', fontSize: '0.85rem' }}
                    onClick={() => handleSelect(site.site_id)}
                  >
                    View Details →
                  </button>
                </td>
              </tr>
            ))}
            {sites.length === 0 && !loading && (
              <tr>
                <td colSpan={7} style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
                  No heritage sites match the selected criteria.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
