import { useCallback, useEffect, useRef, useState } from 'react';
import type { Plan, PlanVm, VmStatus } from './api';

// ---------- formatting ----------
let displayTz = 'Asia/Jakarta';
export const setDisplayTz = (tz: string) => { displayTz = tz || 'Asia/Jakarta'; };
export const getDisplayTz = () => displayTz;
export const tzLabel = (tz = displayTz) => (tz === 'Asia/Jakarta' ? 'WIB' : tz);

export function fmtDate(t?: number | null): string {
  if (!t) return '-';
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: displayTz, day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(t * 1000));
}
export function fmtDateSec(t?: number | null): string {
  if (!t) return '-';
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: displayTz, day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date(t * 1000));
}
export function fmtBytes(n?: number | null): string {
  if (n == null) return '-';
  const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${i === 0 ? v : v.toFixed(2)} ${u[i]}`;
}
export function fmtDuration(sec?: number | null): string {
  if (sec == null) return '-';
  sec = Math.max(0, Math.round(sec));
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (d) return `${d}d ${h}h ${m}m`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}
export function fmtElapsed(p: Plan): string {
  if (!p.started_at) return '0m';
  const syncs = p.vms.map((v) => v.last_sync_at || 0).filter(Boolean);
  const terminal = p.status === 'Completed' || p.status === 'Cancelled';
  const end = terminal ? (syncs.length ? Math.max(...syncs) : null) : Date.now() / 1000;
  if (end == null) return '-';
  const s = Math.max(0, end - p.started_at);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h ${m}m` : h ? `${h}h ${m}m` : `${m}m`;
}
export const fmtMem = (mib?: number | null) => (mib == null ? '-' : mib >= 1024 ? `${+(mib / 1024).toFixed(2)} GiB` : `${mib} MiB`);

// Convert a wall-clock "YYYY-MM-DDTHH:mm" in an IANA zone to epoch seconds.
export function zonedToEpoch(local: string, tz: string): number {
  const [d, t] = local.split('T');
  const [y, mo, da] = d.split('-').map(Number);
  const [h, mi] = (t || '00:00').split(':').map(Number);
  const guess = Date.UTC(y, mo - 1, da, h, mi);
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).formatToParts(new Date(guess));
  const g = (k: string) => Number(parts.find((p) => p.type === k)?.value);
  const asUtc = Date.UTC(g('year'), g('month') - 1, g('day'), g('hour'), g('minute'));
  return (guess - (asUtc - guess)) / 1000;
}
export function epochToZoned(t: number, tz: string): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).formatToParts(new Date(t * 1000));
  const g = (k: string) => parts.find((p) => p.type === k)?.value;
  return `${g('year')}-${g('month')}-${g('day')}T${g('hour')}:${g('minute')}`;
}
export function timezones(): string[] {
  const f = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf;
  const l = f ? f('timeZone') : ['Asia/Jakarta', 'UTC'];
  return l.includes('UTC') ? l : ['UTC', ...l];
}

// ---------- status grouping (same rules as the server's counts) ----------
export type Group = 'in-progress' | 'ready-to-cutover' | 'failed' | 'paused' | 'completed';
export const GROUPS: { slug: Group; label: string }[] = [
  { slug: 'in-progress', label: 'In Progress' },
  { slug: 'ready-to-cutover', label: 'Ready to Cutover' },
  { slug: 'failed', label: 'Failed' },
  { slug: 'paused', label: 'Paused' },
  { slug: 'completed', label: 'Completed' },
];
export function vmGroup(s: VmStatus): Group | null {
  if (['Queued', 'Preparing', 'Seeding', 'Cutover in Progress', 'Testing', 'Rolling Back'].includes(s)) return 'in-progress';
  if (['Ready to Cutover', 'Tested', 'Scheduled for Cutover'].includes(s)) return 'ready-to-cutover';
  if (s === 'Failed') return 'failed';
  if (s === 'Paused') return 'paused';
  if (s === 'Completed') return 'completed';
  return null;
}
export const vmTone = (s: VmStatus): string => {
  if (s === 'Failed') return 'bad';
  if (s === 'Completed') return 'good';
  if (s === 'Paused' || s === 'Cancelled') return 'warn';
  if (s === 'Ready to Cutover' || s === 'Tested' || s === 'Scheduled for Cutover') return 'ready';
  return 'info';
};
export const worstCheck = (v: PlanVm): 'PASS' | 'WARN' | 'FAIL' | null => {
  if (!v.checks?.length) return null;
  if (v.checks.some((c) => c.status === 'FAIL')) return 'FAIL';
  if (v.checks.some((c) => c.status === 'WARN')) return 'WARN';
  return 'PASS';
};

// ---------- live data: SSE `change` + 5 s poll fallback ----------
const subs = new Set<() => void>();
let es: EventSource | null = null;
let pollTimer: number | undefined;
function ensureStream() {
  if (es || typeof EventSource === 'undefined') return;
  es = new EventSource('/api/v2/stream');
  es.addEventListener('change', () => subs.forEach((f) => f()));
  es.onerror = () => { /* the poll timer covers gaps; EventSource reconnects itself */ };
}
function stopStream() { es?.close(); es = null; }
function ensurePoll() {
  if (pollTimer) return;
  pollTimer = window.setInterval(() => {
    if (!es || es.readyState !== EventSource.OPEN) subs.forEach((f) => f());
  }, 5000);
}
function subscribe(f: () => void) {
  subs.add(f); ensureStream(); ensurePoll();
  return () => {
    subs.delete(f);
    if (!subs.size) { stopStream(); window.clearInterval(pollTimer); pollTimer = undefined; }
  };
}

export function useLive<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | undefined>();
  const [error, setError] = useState<Error | undefined>();
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const busy = useRef(false);
  const alive = useRef(true);
  const load = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      const d = await fnRef.current();
      if (alive.current) { setData(d); setError(undefined); }
    } catch (e) {
      if (alive.current) setError(e as Error);
    } finally {
      busy.current = false;
      if (alive.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    alive.current = true;
    setLoading(true);
    load();
    const un = subscribe(load);
    return () => { alive.current = false; un(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading, reload: load, setData };
}

export function downloadBlob(name: string, text: string, type = 'text/csv') {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement('a');
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
