import React from 'react';
import { Link, NavLink, Outlet } from 'react-router-dom';
import { Compass, Landmark, MessageSquare } from 'lucide-react';

export default function ExplorerLayout() {
  return (
    <div className="app-container">
      <nav className="navbar">
        <div className="nav-brand">
          <Landmark size={24} style={{ color: 'var(--primary)' }} />
          <div>
            <Link to="/" style={{ color: 'inherit', textDecoration: 'none' }}>
              <span>TamilHeritage-GPT</span>
            </Link>
            <span style={{ fontSize: '0.75rem', display: 'block', color: 'var(--text-muted)', fontWeight: 400 }}>
              Data & Vector Explorer
            </span>
          </div>
        </div>

        <div className="nav-links">
          <Link to="/" className="nav-link-btn nav-link-chat">
            <MessageSquare size={16} />
            <span>Chat</span>
          </Link>
          <NavLink
            to="/explorer/dashboard"
            className={({ isActive }) => `nav-link-btn ${isActive ? 'active' : ''}`}
          >
            Dashboard
          </NavLink>
          <NavLink
            to="/explorer/sites"
            className={({ isActive }) => `nav-link-btn ${isActive ? 'active' : ''}`}
          >
            Heritage Sites
          </NavLink>
          <NavLink
            to="/explorer/search"
            className={({ isActive }) => `nav-link-btn ${isActive ? 'active' : ''}`}
          >
            Vector Search
          </NavLink>
        </div>
      </nav>

      <main className="main-content">
        <Outlet />
      </main>
    </div>
  );
}
