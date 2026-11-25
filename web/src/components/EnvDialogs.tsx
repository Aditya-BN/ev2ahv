import { useState } from 'react';
import { api, Provider, ProviderIn, ProviderType, Workload } from '../api';
import { Field, Modal, PasswordInput, Spinner, useDialogs } from './ui';
import { fmtBytes, fmtDate, useLive } from '../util';

export const typeLabel = (t: ProviderType) => (t === 'everrun' ? 'Stratus everRun' : t === 'plans' ? 'Plan files (YAML)' : 'Nutanix AOS');

export function EnvDialog({ existing, onClose, onDone }: { existing?: Provider; onClose: () => void; onDone: () => void }) {
  const { showError, toast } = useDialogs();
  const [type, setType] = useState<ProviderType | ''>(existing?.type || '');
  const [name, setName] = useState(existing?.name || '');
  const [address, setAddress] = useState(existing?.address || '');
  const [username, setUsername] = useState(existing?.username || '');
  const [auth, setAuth] = useState<'password' | 'api_key'>(existing?.auth || 'password');
  const [secret, setSecret] = useState('');
  const [nodes, setNodes] = useState((existing?.node_addresses || []).join(', '));
  const [busy, setBusy] = useState(false);
  const everrun = type === 'everrun';
  const files = type === 'plans';
  const nodeList = nodes.split(/[\s,;]+/).filter(Boolean);
  const ok = !!type && name.trim() && address.trim() && (files || (username.trim() && (existing || secret))) && (!everrun || nodeList.length > 0);

  const body = (): ProviderIn => ({
    name: name.trim(), type: type as ProviderType, address: address.trim(), username: files ? '-' : username.trim(),
    password: files ? null : auth === 'password' || everrun ? secret || null : null,
    api_key: !everrun && !files && auth === 'api_key' ? secret || null : null,
    node_addresses: everrun ? nodeList : [],
  });
  const submit = async () => {
    setBusy(true);
    try {
      if (existing) await api.editProvider(existing.id, body()); else await api.addProvider(body());
      toast(existing ? 'Environment updated' : 'Environment added');
      onDone(); onClose();
    } catch (e) { showError(e, existing ? 'Edit Environment' : 'Add Environment'); }
    finally { setBusy(false); }
  };
  return (
    <Modal title={existing ? 'Edit Environment Details' : 'Add Environment'} width={540} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!ok || busy} onClick={submit}>{existing ? 'Save' : 'Add'}</button></>}>
      <h4 className="section">Details</h4>
      <Field label="Select Environment Type">
        <select id="env-type" value={type} disabled={!!existing} onChange={(e) => { setType(e.target.value as ProviderType); setAuth('password'); }}>
          <option value="">Select Environment Type</option>
          <option value="everrun">Stratus everRun</option>
          <option value="aos">Nutanix AOS</option>
          <option value="plans">Plan files (YAML)</option>
        </select>
      </Field>
      {type && (
        <>
          <Field label="Environment Name"><input value={name} placeholder="Enter a friendly display name" onChange={(e) => setName(e.target.value)} /></Field>
          <Field label={everrun ? 'everRun System IP (management address)' : files ? 'Plans directory on the server' : 'Prism Central Address'}>
            <input value={address} placeholder={files ? 'e.g. /home/user/ev2ahv/plans' : 'Enter IP Address or FQDN'} onChange={(e) => setAddress(e.target.value)} /></Field>
          {!files && <Field label="User Name"><input value={username} placeholder="Enter user name" onChange={(e) => setUsername(e.target.value)} /></Field>}
          {!everrun && !files && (
            <div className="field"><span className="field-label">Authentication</span>
              <span className="radios">
                <label><input type="radio" checked={auth === 'password'} onChange={() => setAuth('password')} /> Password</label>
                <label><input type="radio" checked={auth === 'api_key'} onChange={() => setAuth('api_key')} /> API key</label>
              </span></div>
          )}
          {!files && <Field label={!everrun && auth === 'api_key' ? 'API Key' : 'Password'}
            hint={existing ? 'Leave empty to keep the stored secret.' : undefined}>
            <PasswordInput value={secret} onChange={setSecret} placeholder={!everrun && auth === 'api_key' ? 'Enter API key' : 'Enter password'} /></Field>}
          {everrun && (
            <Field label="Node addresses" hint="everRun node IPs used for read-only disk access. Separate with commas or spaces.">
              <input value={nodes} placeholder="e.g. 10.0.0.11, 10.0.0.12" onChange={(e) => setNodes(e.target.value)} /></Field>
          )}
        </>
      )}
    </Modal>
  );
}

export function EnvView({ env, onClose }: { env: Provider; onClose: () => void }) {
  const { data, loading } = useLive<Workload[]>(async () => (env.role === 'source' ? (await api.workloads(env.id)).entities : []), [env.id]);
  return (
    <Modal title={`Environment Details: ${env.name}`} width={820} onClose={onClose} footer={<button className="btn primary" onClick={onClose}>OK</button>}>
      <dl className="kv">
        <dt>Environment Type</dt><dd>{typeLabel(env.type)}</dd>
        <dt>Name</dt><dd>{env.name}</dd>
        <dt>Address</dt><dd>{env.address}</dd>
        <dt>User Name</dt><dd>{env.username} ({env.auth === 'api_key' ? 'API key' : 'password'})</dd>
        <dt>Status</dt><dd>{env.status}{env.error ? ` (${env.error})` : ''}</dd>
        <dt>Version</dt><dd>{env.version || '-'}</dd>
        <dt>Last refreshed</dt><dd>{fmtDate(env.last_refreshed)}</dd>
        {env.role === 'source' && <><dt>Node addresses</dt><dd>{env.node_addresses.join(', ') || '-'}</dd></>}
        {env.role === 'target' && <>
          <dt>Clusters</dt><dd>{env.clusters.map((c) => c.name).join(', ') || '-'}</dd>
          <dt>Containers</dt><dd>{env.containers.map((c) => c.name).join(', ') || '-'}</dd>
          <dt>Subnets</dt><dd>{env.subnets.map((c) => c.name).join(', ') || '-'}</dd></>}
      </dl>
      {env.role === 'source' && (
        <>
          <h4 className="section">VMs ({env.vm_count})</h4>
          {loading ? <Spinner /> : (
            <div className="table-wrap small-table"><table className="grid"><thead><tr>
              <th>Name</th><th>OS</th><th>Availability</th><th>Power State</th><th>vCPU(s)</th><th>Memory</th><th>Storage</th></tr></thead>
              <tbody>{(data || []).map((w) => (
                <tr key={w.id}><td>{w.name}</td><td>{w.os_name}</td><td>{w.availability || '-'}</td><td>{w.power_state}</td>
                  <td>{w.vcpus}</td><td>{w.memory_mib / 1024} GiB</td><td>{fmtBytes(w.total_bytes)}</td></tr>))}</tbody></table></div>
          )}
        </>
      )}
    </Modal>
  );
}
