import React from 'react';
import { Link } from 'react-router-dom';
import { ExternalLink } from 'lucide-react';

export default function SourceCard({ citation }) {
  const {
    site_id,
    site_name_en,
    site_name_ta,
    section,
    lang,
    score,
    trust,
    origin,
    source_url,
  } = citation;

  // Percentage for score bar (score typically 0.45 - 1.0)
  const scorePct = Math.min(100, Math.max(10, Math.round((score || 0) * 100)));

  // Trust badge styling
  let trustClass = 'trust-unverified';
  let trustLabel = 'Unverified';
  if (trust === 'official') {
    trustClass = 'trust-official';
    trustLabel = 'Official';
  } else if (trust === 'multi_source') {
    trustClass = 'trust-multi';
    trustLabel = 'Multi-source';
  } else if (trust === 'single_source') {
    trustClass = 'trust-single';
    trustLabel = 'Single-source';
  }

  // Language display
  const langLabels = {
    ta: 'தமிழ்',
    en: 'EN',
    kn: 'ಕನ್ನಡ',
    'ta-Latn': 'Tanglish',
  };

  return (
    <div className="source-card">
      <div className="source-card-header">
        <div className="source-card-title">
          <Link to={`/explorer/sites/${encodeURIComponent(site_id)}`} className="source-site-link">
            {site_name_en}
          </Link>
          {site_name_ta && <span className="source-site-ta">({site_name_ta})</span>}
        </div>
        {source_url && (
          <a
            href={source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="source-external-link"
            title="Open original source"
            aria-label={`Open original source for ${site_name_en}`}
          >
            <ExternalLink size={14} />
          </a>
        )}
      </div>

      <div className="source-card-badges">
        <span className="badge badge-section">{section}</span>
        <span className="badge badge-lang">{langLabels[lang] || lang}</span>
        <span className={`badge ${trustClass}`}>{trustLabel}</span>
        {origin && <span className="badge badge-origin">{origin}</span>}
      </div>

      <div className="source-score-container" title={`Relevance score: ${(score || 0).toFixed(4)}`}>
        <div className="source-score-label">
          <span>Relevance</span>
          <span>{((score || 0) * 100).toFixed(1)}%</span>
        </div>
        <div className="source-score-bar-bg">
          <div className="source-score-bar-fill" style={{ width: `${scorePct}%` }} />
        </div>
      </div>
    </div>
  );
}
