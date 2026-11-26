import { useState } from 'react';
import { api, UiUser } from '../api';
import { useSession } from '../App';
import { Field, Modal, PasswordInput, Spinner, useDialogs } from '../components/ui';
import { fmtDateSec, useLive } from '../util';

// D13: local users with an admin or viewer role. Viewers can read everything but change nothing.
export default function Users() {
  const { info } = useSession();
  const { showError, toast, confirm } = useDialogs();
  const { data, loading, reload } = useLive(() => api.users(), []);
  const [dlg, setDlg] = useState<'' | 'add' | { reset: string }>('');
  const [name, setName] = useState('');
  const [role, setRole] = useState<'viewer' | 'admin'>('viewer');
  const [pw, setPw] = useState('');
  const close = () => { setDlg(''); setName(''); setPw(''); setRole('viewer'); };
  const run = async (what: string, fn: () => Promise<unknown>) => {
    try { await fn(); toast(what); close(); reload(); } catch (e) { showError(e, 'Users'); }
  };
  const setUserRole = (u: UiUser, r: 'admin' | 'viewer') => run(`${u.username} is now ${r}`, () => api.updateUser(u.username, { role: r }));
  const remove = async (u: UiUser) => {
    if (await confirm({ title: 'Delete user', body: `Delete user ${u.username}? Their sessions end at once.`, ok: 'Delete', danger: true })) run(`${u.username} deleted`, () => api.deleteUser(u.username));
  };
  return (
    <div className="content">
      <div className="main-col">
        <h2 className="page-title">Users</h2>
        <div className="list-head">
          <p className="muted">Admins can change everything. Viewers can see plans, events and logs but cannot start, change or delete anything. New users and password resets must choose a new password at first login.</p>
          <div className="spacer" />
          <button className="btn primary" onClick={() => setDlg('add')}>Add user</button>
        </div>
        <div className="table-wrap">
          <table className="grid">
            <thead><tr><th>User</th><th>Role</th><th>Password</th><th>Created</th><th /></tr></thead>
            <tbody>
              {loading && <tr><td colSpan={5}><Spinner /></td></tr>}
              {(data?.entities || []).map((u) => (
                <tr key={u.username}>
                  <td>{u.username}{u.username === info.user && <span className="muted"> (you)</span>}</td>
                  <td><select aria-label={`Role of ${u.username}`} value={u.role} onChange={(e) => setUserRole(u, e.target.value as 'admin' | 'viewer')}>
                    <option value="admin">Admin</option><option value="viewer">Viewer</option></select></td>
                  <td>{u.must_change ? <span className="muted">must change at next login</span> : 'set'}</td>
                  <td className="nowrap">{u.created_at ? fmtDateSec(u.created_at) : '-'}</td>
                  <td className="row-actions">
                    <button className="btn" onClick={() => setDlg({ reset: u.username })}>Reset password</button>{' '}
                    <button className="btn" disabled={u.username === info.user} onClick={() => remove(u)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted">LDAP / Active Directory sign-in is not available yet; see docs/security.md.</p>
      </div>
      {dlg === 'add' && (
        <Modal title="Add user" width={440} onClose={close}
          footer={<><button className="btn" onClick={close}>Cancel</button>
            <button className="btn primary" disabled={!name || pw.length < 8} onClick={() => run(`User ${name} added`, () => api.addUser(name, pw, role))}>Add</button></>}>
          <Field label="User name"><input value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" /></Field>
          <Field label="Role"><select value={role} onChange={(e) => setRole(e.target.value as 'viewer' | 'admin')}><option value="viewer">Viewer (read only)</option><option value="admin">Admin</option></select></Field>
          <Field label="Temporary password" hint="At least 8 characters. The user changes it at first login."><PasswordInput value={pw} onChange={setPw} autoComplete="new-password" /></Field>
        </Modal>
      )}
      {typeof dlg === 'object' && (
        <Modal title={`Reset password of ${dlg.reset}`} width={440} onClose={close}
          footer={<><button className="btn" onClick={close}>Cancel</button>
            <button className="btn primary" disabled={pw.length < 8} onClick={() => run(`Password of ${dlg.reset} reset`, () => api.updateUser(dlg.reset, { password: pw }))}>Reset</button></>}>
          <Field label="Temporary password" hint={dlg.reset === info.user ? 'At least 8 characters.' : 'At least 8 characters. Their sessions end and they change it at next login.'}><PasswordInput value={pw} onChange={setPw} autoComplete="new-password" /></Field>
        </Modal>
      )}
    </div>
  );
}
