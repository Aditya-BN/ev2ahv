import { createContext, useContext, useEffect, useState } from 'react';
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { api, authEvents, AppInfo } from './api';
import { Spinner } from './components/ui';
import { setDisplayTz, useLive } from './util';
import Login from './pages/Login';
import NavRail from './components/NavRail';
import Dashboard from './pages/Dashboard';
import Wizard from './pages/Wizard';
import PlanDetails from './pages/PlanDetails';
import Progress from './pages/Progress';
import AllPlanVms from './pages/AllPlanVms';
import Events from './pages/Events';
import Bandwidth from './pages/Bandwidth';
import Logs from './pages/Logs';
import ApplianceSettings from './pages/ApplianceSettings';
import ForcePassword from './pages/ForcePassword';
import Health from './pages/Health';

export const SessionCtx = createContext<{ info: AppInfo; logout: () => void }>(null as never);
export const useSession = () => useContext(SessionCtx);

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="shell"><NavRail /><main className="page">{children}</main></div>;
}

export default function App() {
  const [info, setInfo] = useState<AppInfo | null | undefined>(undefined);
  const nav = useNavigate();
  const loc = useLocation();

  const refresh = () => api.appinfo().then(setInfo).catch(() => setInfo(null));
  useEffect(() => { refresh(); }, []);
  useEffect(() => {
    const h = () => setInfo((i) => (i ? { ...i, user: null } : i));
    const m = () => setInfo((i) => (i ? { ...i, must_change: true } : i));
    authEvents.addEventListener('unauthorized', h);
    authEvents.addEventListener('must_change', m);
    return () => { authEvents.removeEventListener('unauthorized', h); authEvents.removeEventListener('must_change', m); };
  }, []);

  if (info === undefined) return <div className="boot"><Spinner text="Loading..." /></div>;
  if (!info || !info.user) {
    return <Login mode={info?.mode} onDone={() => { refresh(); }} />;
  }
  if (info.must_change) return <ForcePassword user={info.user} mode={info.mode} onDone={() => { refresh(); nav('/dashboard'); }} onLogout={async () => { try { await api.logout(); } catch { /* ignore */ } setInfo({ ...info, user: null, must_change: false }); }} />;
  const logout = async () => {
    try { await api.logout(); } catch { /* ignore */ }
    setInfo({ ...info, user: null });
    nav('/dashboard');
  };
  return (
    <SessionCtx.Provider value={{ info, logout }}>
      <TzSync />
      <Routes key={loc.pathname.startsWith('/migration') ? 'full' : 'shell'}>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Shell><Dashboard /></Shell>} />
        <Route path="/migrationplan" element={<Wizard />} />
        <Route path="/migrationplan/:id" element={<Wizard />} />
        <Route path="/plan-details/:id" element={<Shell><PlanDetails /></Shell>} />
        <Route path="/migrationprogress/:id" element={<Progress />} />
        <Route path="/all-plan-vms/:status" element={<Shell><AllPlanVms /></Shell>} />
        <Route path="/events" element={<Shell><Events /></Shell>} />
        <Route path="/bandwidth-configuration" element={<Shell><Bandwidth /></Shell>} />
        <Route path="/realtime-logs" element={<Shell><Logs /></Shell>} />
        <Route path="/appliance-settings" element={<Shell><ApplianceSettings /></Shell>} />
        <Route path="/health" element={<Shell><Health /></Shell>} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </SessionCtx.Provider>
  );
}

function TzSync() {
  const { data } = useLive(() => api.settings(), []);
  if (data?.timezone) setDisplayTz(data.timezone);
  return null;
}
