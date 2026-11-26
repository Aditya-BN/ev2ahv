import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { Pager, Spinner, usePager } from '../components/ui';
import { downloadBlob, fmtDateSec, tzLabel, useLive } from '../util';

const SEV = ['info', 'warning', 'error'] as const;

export default function Events() {
  const { data, loading } = useLive(() => api.events(1000), []);
  const [q, setQ] = useState('');
  const [sev, setSev] = useState<Set<string>>(new Set());
  const [asc, setAsc] = useState(false);
  const all = data?.entities || [];
  const list = useMemo(() => {
    const l = all.filter((e) => (sev.size === 0 || sev.has(e.severity)) &&
      `${e.plan || ''} ${e.vm || ''} ${e.message}`.toLowerCase().includes(q.toLowerCase()));
    return [...l].sort((a, b) => (asc ? a.time - b.time : b.time - a.time));
  }, [all, q, sev, asc]);
  const pager = usePager(list.length, [q, sev]);
  const rows = pager.slice(list);
  const exportCsv = () => {
    const esc = (s: string) => `"${s.replace(/"/g, '""')}"`;
    const lines = ['time_utc,time_wib,severity,plan,vm,message', ...list.map((e) =>
      [new Date(e.time * 1000).toISOString(), fmtDateSec(e.time), e.severity, e.plan || '', e.vm || '', e.message].map((x) => esc(String(x))).join(','))];
    downloadBlob('ev2ahv-events.csv', lines.join('\n') + '\n');
  };
  const toggle = (k: string) => { const n = new Set(sev); n.has(k) ? n.delete(k) : n.add(k); setSev(n); };
  return (
    <div className="content">
      <div className="main-col">
        <h2 className="page-title">Events</h2>
                <div className="list-head">
          <div className="search wide"><span>&#128269;</span><input value={q} placeholder="Type to filter events by plan, VM or message" onChange={(e) => setQ(e.target.value)} /></div>
          <div className="spacer" />
          <button className="btn" onClick={exportCsv}>Export as CSV</button>
          <Pager pager={pager} total={list.length} />
        </div>
        <div className="filter-bar" role="group" aria-label="Severity filter">
          <button className={'chip' + (sev.size === 0 ? ' on' : '')} aria-pressed={sev.size === 0} onClick={() => { setSev(new Set()); setQ(''); }}>All severities</button>
          {SEV.map((s) => <button key={s} className={'chip' + (sev.has(s) ? ' on' : '')} aria-pressed={sev.has(s)} onClick={() => toggle(s)}>{s[0].toUpperCase() + s.slice(1)}</button>)}
        </div>
        <div className="muted viewing">viewing all {list.length} events. Times are shown in {tzLabel()} ({'Asia/Jakarta'}); logs on the server use UTC.</div>
        <div className="table-wrap">
          <table className="grid">
            <thead><tr><th>Severity</th><th className="sortable" onClick={() => setAsc(!asc)}>Time ({tzLabel()}) {asc ? '▲' : '▼'}</th><th>Migration Plan</th><th>VM</th><th>Message</th></tr></thead>
            <tbody>
              {loading && <tr><td colSpan={5}><Spinner /></td></tr>}
              {rows.map((e, i) => (
                <tr key={i}>
                  <td><span className={'sev ' + e.severity}>{e.severity === 'info' ? 'Info' : e.severity === 'warning' ? 'Warning ▲' : 'Error ▲'}</span></td>
                  <td className="nowrap">{fmtDateSec(e.time)}</td><td>{e.plan || <span className="muted">Not Available</span>}</td>
                  <td>{e.vm || <span className="muted">Not Available</span>}</td><td>{e.message}</td>
                </tr>
              ))}
              {!loading && rows.length === 0 && <tr><td colSpan={5} className="empty">No events.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
