import React from 'react';

export default function Navbar({ activeTab, setActiveTab }) {
  return (
    <nav className="navbar">
      <div className="nav-brand">
        <span style={{ fontSize: '1.5rem' }}>🏛️</span>
        <div>
          <span>TamilHeritage-GPT</span>
          <span style={{ fontSize: '0.75rem', display: 'block', color: 'var(--text-muted)', fontWeight: 400 }}>
            Level 1: Multilingual RAG Explorer
          </span>
        </div>
      </div>
      <div className="nav-links">
        <button
          className={activeTab === 'dashboard' ? 'active' : ''}
          onClick={() => setActiveTab('dashboard')}
        >
          Dashboard & Health
        </button>
        <button
          className={activeTab === 'sites' || activeTab === 'site-detail' ? 'active' : ''}
          onClick={() => setActiveTab('sites')}
        >
          Heritage Sites
        </button>
        <button
          className={activeTab === 'search' ? 'active' : ''}
          onClick={() => setActiveTab('search')}
        >
          Semantic Search
        </button>
      </div>
    </nav>
  );
}
