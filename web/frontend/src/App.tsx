import { useCallback, useEffect, useState } from 'react';
import Sidebar from './components/Sidebar';
import type { TabKey } from './components/Sidebar';
import Login from './pages/Login';
import Inventory from './pages/Inventory';
import Shelves from './pages/Shelves';
import Orders from './pages/Orders';
import UserRegister from './pages/UserRegister';
import Profile from './pages/Profile';
import { getJSON } from './api';
import { EMPTY_ALERTS } from './types';
import type { Alerts, SessionUser } from './types';
import { useWebSocket } from './useWebSocket';

const STORAGE_KEY = 'currentUser';

function loadSavedUser(): SessionUser | null {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as SessionUser;
  } catch {
    localStorage.removeItem(STORAGE_KEY);
    return null;
  }
}

export default function App() {
  const [user, setUser] = useState<SessionUser | null>(loadSavedUser);
  const [activeTab, setActiveTab] = useState<TabKey>('inventory');
  const [alerts, setAlerts] = useState<Alerts>(EMPTY_ALERTS);

  const refreshAlerts = useCallback(async () => {
    try {
      setAlerts(await getJSON<Alerts>('/api/chemicals/alerts'));
    } catch {
      // 폴링 실패는 조용히 무시
    }
  }, []);

  useWebSocket(() => {
    if (user) refreshAlerts();
  });

  // 알림 폴링 (5초)
  useEffect(() => {
    if (!user) return;
    refreshAlerts();
    const timer = setInterval(refreshAlerts, 5000);
    return () => clearInterval(timer);
  }, [user, refreshAlerts]);

  const handleLoginSuccess = (loggedIn: SessionUser) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(loggedIn));
    setUser(loggedIn);
  };

  const handleProfileUpdated = (updated: SessionUser) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
    setUser(updated);
  };

  const handleLogout = () => {
    localStorage.removeItem(STORAGE_KEY);
    setUser(null);
    setActiveTab('inventory');
  };

  if (!user) {
    return <Login onLoginSuccess={handleLoginSuccess} />;
  }

  return (
    <div className="app">
      <Sidebar
        activeTab={activeTab}
        onSelect={setActiveTab}
        user={user}
        onProfile={() => setActiveTab('profile')}
        onLogout={handleLogout}
      />
      {activeTab === 'inventory' && (
        <Inventory alerts={alerts} refreshAlerts={refreshAlerts} user={user} />
      )}
      {activeTab === 'shelves' && <Shelves />}
      {activeTab === 'orders' && <Orders />}
      {activeTab === 'users' && <UserRegister />}
      {activeTab === 'profile' && <Profile user={user} onUpdated={handleProfileUpdated} />}
    </div>
  );
}
