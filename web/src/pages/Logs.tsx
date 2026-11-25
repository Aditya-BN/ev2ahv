import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';

export default function Logs() {
  const [text, setText] = useState('Loading...');
  const [auto, setAuto] = useState(true);
  const [err, setErr] = useState('');
  const ref = useRef<HTMLPreElement>(null);
  const load = () => api.logs(500).then((t) => { setText(t || '(no log lines)'); setErr(''); }).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!auto) return;
    const t = window.setInterval(load, 3000);
    return () => window.clearInterval(t);
  }, [auto]);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [text]);
  return (
    <div className="content">
      <Link to="/dashboard" className="back">&#8249; Back to Dashboard</Link>
      <div className="title-row"><h2 className="page-title">Realtime Logs</h2><div className="spacer" />
        <label className="cb"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Auto refresh (3 s)</label>
        <button className="btn" onClick={load}>Refresh</button></div>
      <p className="muted">Timestamps in this log are UTC.</p>
      {err && <div className="alert bad">{err}</div>}
      <pre className="logview" ref={ref}>{text}</pre>
    </div>
  );
}
