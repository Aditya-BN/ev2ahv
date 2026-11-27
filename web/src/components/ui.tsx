import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { ApiError } from '../api';
import { pageZoom } from '../util';

// ---------- Modal ----------
export function Modal(props: {
  title: ReactNode; onClose?: () => void; children: ReactNode; footer?: ReactNode; width?: number; tone?: 'danger';
}) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape' && props.onClose) props.onClose(); };
    document.addEventListener('keydown', k);
    return () => document.removeEventListener('keydown', k);
  }, [props.onClose]);
  return (
    <div className="modal-backdrop">
      <div className={'modal' + (props.tone ? ' ' + props.tone : '')} style={{ width: props.width || 500 }} role="dialog" aria-modal="true">
        <div className="modal-head">
          <h3>{props.title}</h3>
          {props.onClose && <button className="icon-btn" aria-label="Close" onClick={props.onClose}>&#x2715;</button>}
        </div>
        <div className="modal-body">{props.children}</div>
        {props.footer && <div className="modal-foot">{props.footer}</div>}
      </div>
    </div>
  );
}

// ---------- Menu (kebab / dropdown button) ----------
export function Menu(props: {
  label: ReactNode; items: { label: string; onClick?: () => void; href?: string; danger?: boolean; disabled?: boolean; divider?: boolean }[];
  kind?: 'link' | 'btn' | 'icon'; disabled?: boolean; align?: 'left' | 'right'; id?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [open]);
  const cls = props.kind === 'btn' ? 'btn' : props.kind === 'icon' ? 'icon-btn' : 'linkbtn';
  return (
    <div className="menu" ref={ref}>
      <button id={props.id} className={cls} disabled={props.disabled} onClick={() => setOpen(!open)} aria-haspopup="menu" aria-expanded={open}>
        {props.label}{props.kind !== 'icon' && <span className="caret">&#9662;</span>}
      </button>
      {open && (
        <ul className={'menu-list ' + (props.align === 'right' ? 'right' : '')} role="menu">
          {props.items.map((it, i) =>
            it.divider ? <li key={i} className="menu-div" /> : (
              <li key={i}>
                {it.href ? (
                  <a className="menu-item" role="menuitem" href={it.href} target={it.href.startsWith('/api') ? undefined : '_blank'} rel="noreferrer" onClick={() => setOpen(false)}>{it.label}</a>
                ) : (
                  <button className={'menu-item' + (it.danger ? ' danger' : '')} role="menuitem" disabled={it.disabled}
                    onClick={() => { setOpen(false); it.onClick?.(); }}>{it.label}</button>
                )}
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}
export const Kebab = '⋮';

// ---------- Split button: primary action plus a menu of the rest ----------
export function SplitButton(props: {
  label: ReactNode; onClick: () => void; items: { label: string; onClick?: () => void; href?: string; danger?: boolean; disabled?: boolean }[];
  disabled?: boolean; id?: string; moreLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', h); document.addEventListener('keydown', k);
    return () => { document.removeEventListener('mousedown', h); document.removeEventListener('keydown', k); };
  }, [open]);
  return (
    <div className="split menu" ref={ref}>
      <button id={props.id} className="split-main" disabled={props.disabled} onClick={props.onClick}>{props.label}</button>
      <button className="split-more" disabled={props.disabled || props.items.length === 0} aria-haspopup="menu" aria-expanded={open}
        aria-label={props.moreLabel || 'More actions'} onClick={() => setOpen(!open)}>&#9662;</button>
      {open && (
        <ul className="menu-list right" role="menu">
          {props.items.map((it, i) => (
            <li key={i}><button className={'menu-item' + (it.danger ? ' danger' : '')} role="menuitem" disabled={it.disabled}
              onClick={() => { setOpen(false); it.onClick?.(); }}>{it.label}</button></li>
          ))}
        </ul>
      )}
    </div>
  );
}


// ---------- Tip (hover tooltip) and Popover (click) ----------
export function Tip(props: { text: ReactNode; children: ReactNode; wide?: boolean }) {
  const [pos, setPos] = useState<null | { x: number; y: number }>(null);
  const w = props.wide ? 320 : 240;
  return (
    <span className="tip" onMouseEnter={(e) => {
      const r = (e.currentTarget as HTMLElement).getBoundingClientRect(), z = pageZoom();
      setPos({ x: Math.max(8, Math.min(window.innerWidth / z - w - 8, (r.left + r.width / 2) / z - w / 2)), y: r.bottom / z + 6 });
    }} onMouseLeave={() => setPos(null)}>
      {props.children}
      {pos && <span className="tip-box" style={{ left: pos.x, top: pos.y, width: w }} role="tooltip">{props.text}</span>}
    </span>
  );
}
export function Popover(props: { trigger: ReactNode; children: ReactNode; title?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [open]);
  return (
    <span className="popover-wrap" ref={ref}>
      <button className="popover-trigger" onClick={() => setOpen(!open)}>{props.trigger}</button>
      {open && <div className="popover">{props.title && <div className="popover-title">{props.title}</div>}{props.children}</div>}
    </span>
  );
}

// ---------- Pager ----------
export function usePager(total: number, deps: unknown[] = []) {
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(10);
  useEffect(() => { setPage(0); /* eslint-disable-next-line */ }, deps);
  const pages = Math.max(1, Math.ceil(total / size));
  const p = Math.min(page, pages - 1);
  const from = total ? p * size + 1 : 0;
  const to = Math.min(total, (p + 1) * size);
  return { page: p, size, setSize, setPage, pages, from, to, slice: <T,>(a: T[]) => a.slice(p * size, p * size + size) };
}
export function Pager(props: { pager: ReturnType<typeof usePager>; total: number; showSize?: boolean }) {
  const { pager: g, total } = props;
  return (
    <div className="pager">
      <button className="round" aria-label="Previous page" disabled={g.page === 0} onClick={() => g.setPage(g.page - 1)}>&#8249;</button>
      <span>{g.from} - {g.to} of {total}</span>
      <button className="round" aria-label="Next page" disabled={g.page >= g.pages - 1} onClick={() => g.setPage(g.page + 1)}>&#8250;</button>
      {props.showSize !== false && (
        <label className="pager-size">Show:{' '}
          <select value={g.size} onChange={(e) => { g.setSize(Number(e.target.value)); g.setPage(0); }}>
            {[10, 25, 50, 100].map((n) => <option key={n} value={n}>{n} rows</option>)}
          </select>
        </label>
      )}
    </div>
  );
}

// ---------- small bits ----------
export function Spinner({ text }: { text?: string }) {
  return <div className="spinner-row"><span className="spinner" />{text && <span>{text}</span>}</div>;
}
export function Badge(props: { tone: string; children: ReactNode }) {
  return <span className={'badge ' + props.tone}>{props.children}</span>;
}
export function Field(props: { label: string; hint?: ReactNode; children: ReactNode; error?: string }) {
  return (
    <label className="field">
      <span className="field-label">{props.label}</span>
      {props.children}
      {props.hint && <span className="field-hint">{props.hint}</span>}
      {props.error && <span className="field-error">{props.error}</span>}
    </label>
  );
}
export function PasswordInput(props: { value: string; onChange: (v: string) => void; placeholder?: string; id?: string; autoComplete?: string }) {
  const [show, setShow] = useState(false);
  return (
    <span className="pw">
      <input id={props.id} type={show ? 'text' : 'password'} value={props.value} placeholder={props.placeholder}
        autoComplete={props.autoComplete || 'new-password'} onChange={(e) => props.onChange(e.target.value)} />
      <button type="button" className="linkbtn small" onClick={() => setShow(!show)}>{show ? 'Hide' : 'Show'}</button>
    </span>
  );
}

// ---------- error + confirm dialogs ----------
interface Ctx {
  showError: (e: unknown, title?: string) => void;
  confirm: (o: { title: string; body: ReactNode; ok: string; cancel?: string; danger?: boolean; dismiss?: boolean }) => Promise<boolean>;
  toast: (m: string) => void;
}
const DialogCtx = createContext<Ctx>({ showError: () => {}, confirm: async () => false, toast: () => {} });
export const useDialogs = () => useContext(DialogCtx);

export function DialogProvider({ children }: { children: ReactNode }) {
  const [err, setErr] = useState<{ title: string; msg: string; failing: string[] } | null>(null);
  const [conf, setConf] = useState<null | { o: Parameters<Ctx['confirm']>[0]; res: (b: boolean) => void }>(null);
  const [toasts, setToasts] = useState<{ id: number; m: string }[]>([]);
  const showError = useCallback((e: unknown, title = 'Error') => {
    const a = e as ApiError;
    setErr({ title, msg: a?.message || String(e), failing: a?.failing || [] });
  }, []);
  const confirm = useCallback<Ctx['confirm']>((o) => new Promise((res) => setConf({ o, res })), []);
  const toast = useCallback((m: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, m }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3500);
  }, []);
  return (
    <DialogCtx.Provider value={{ showError, confirm, toast }}>
      {children}
      {err && (
        <Modal title={err.title} width={520} tone="danger" onClose={() => setErr(null)}
          footer={<button className="btn primary" autoFocus onClick={() => setErr(null)}>OK</button>}>
          <p className="err-msg">{err.msg}</p>
          {err.failing.length > 0 && <ul className="plain">{err.failing.map((f) => <li key={f}>{f}</li>)}</ul>}
        </Modal>
      )}
      {conf && (
        <Modal title={conf.o.title} width={500} onClose={() => { conf.res(conf.o.dismiss ?? false); setConf(null); }}
          footer={<>
            <button className="btn" onClick={() => { conf.res(false); setConf(null); }}>{conf.o.cancel || 'Cancel'}</button>
            <button className={'btn ' + (conf.o.danger ? 'danger' : 'primary')} autoFocus onClick={() => { conf.res(true); setConf(null); }}>{conf.o.ok}</button>
          </>}>
          {conf.o.body}
        </Modal>
      )}
      <div className="toasts">{toasts.map((t) => <div key={t.id} className="toast">{t.m}</div>)}</div>
    </DialogCtx.Provider>
  );
}
