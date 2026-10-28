"use client";
import {useEffect, useState} from 'react';
import {motionPolicy} from '@/lib/api';
import {useI18n} from '@/lib/i18n';

type Policy = {id: string; robot?: string; revision?: string; conditions?: string;
  error?: string; selected?: boolean; check: {state: string}};

export default function MotionPolicies({source}: {source: string}) {
  const {t} = useI18n();
  const [items, setItems] = useState<Policy[]>([]);
  const [draft, setDraft] = useState({id: '', path: source, conditions: ''});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const refresh = () => motionPolicy('policies').then(v => setItems(v.policies));
  useEffect(() => {refresh().catch(e => setError(e.message));}, []);
  useEffect(() => {if (source) setDraft(v => ({...v, path: source}));}, [source]);
  async function perform(operation: string, payload: Record<string, unknown>) {
    setBusy(true); setError('');
    try {await motionPolicy(operation, payload); await refresh();}
    catch (e) {setError((e as Error).message);}
    finally {setBusy(false);}
  }
  return <section className="space-y-4">
    <p className="muted">{t('ONNX motion policies require policy.onnx, contract.json and release.yaml. Importing files does not enable motion.')}</p>
    <h2>{t('Imported policies')}</h2>
    {!items.length && <p className="muted">{t('No policies imported yet.')}</p>}
    {items.map(item => <article key={item.id} className="settings-row">
      <div><b>{item.id}</b><p className="muted">{item.robot} · {item.conditions}</p>
        <p>{t(item.check.state === 'loaded' ? 'Native inference check passed' : 'Needs setup')}</p>
        <details><summary>{t('Files and revision')}</summary><code className="break-all text-xs">{item.revision}</code></details>
        {item.error && <p className="error-text">{item.error}</p>}
      </div>
      <div className="flex gap-2"><button className="nerv-control action" disabled={busy || !!item.error}
        onClick={() => perform('check', {id: item.id})}>{t('Check')}</button>
        <button className="nerv-control action" disabled={busy || !!item.error || item.check.state !== 'loaded' || item.selected}
          onClick={() => perform('select', {id: item.id, revision: item.revision})}>{t(item.selected ? 'Selected policy revision' : 'Use this policy revision')}</button></div>
    </article>)}
    <details open={!items.length || !!draft.path}><summary>{t('Import local policy')}</summary>
      <label className="form-label">{t('Policy ID')}<input className="form-field" value={draft.id} onChange={e => setDraft({...draft, id: e.target.value})}/></label>
      <label className="form-label">{t('Task and starting conditions')}<input className="form-field" value={draft.conditions} onChange={e => setDraft({...draft, conditions: e.target.value})}/></label>
      <label className="form-label">{t('Policy directory')}<input className="form-field" value={draft.path} onChange={e => setDraft({...draft, path: e.target.value})}/></label>
      <button className="nerv-control action" disabled={busy || !draft.id.trim() || !draft.path.trim()}
        onClick={() => perform('import', draft)}>{t('Import local policy')}</button>
    </details>
    {error && <p className="error-text" role="alert">{error}</p>}
  </section>;
}
