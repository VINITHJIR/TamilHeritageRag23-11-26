import React, { useState } from 'react';
import Navbar from './components/Navbar';
import Dashboard from './pages/Dashboard';
import Sites from './pages/Sites';
import SiteDetail from './pages/SiteDetail';
import Search from './pages/Search';

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [selectedSiteId, setSelectedSiteId] = useState(null);

  const handleSelectSite = (siteId) => {
    setSelectedSiteId(siteId);
    setActiveTab('site-detail');
  };

  return (
    <div className="app-container">
      <Navbar activeTab={activeTab} setActiveTab={setActiveTab} />
      <main className="main-content">
        {activeTab === 'dashboard' && <Dashboard />}
        {activeTab === 'sites' && <Sites onSelectSite={handleSelectSite} />}
        {activeTab === 'site-detail' && (
          <SiteDetail
            siteId={selectedSiteId}
            onBack={() => setActiveTab('sites')}
          />
        )}
        {activeTab === 'search' && <Search onSelectSite={handleSelectSite} />}
      </main>
    </div>
  );
}
