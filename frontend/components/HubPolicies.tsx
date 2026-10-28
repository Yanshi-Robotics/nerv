"use client";
import { useEffect, useState } from 'react';
import { storageApi, moduleCatalog, pauseModuleInstall, cancelModuleInstall, resumeModuleInstall } from '@/lib/api';
import { type InstallJob, watchInstallLog } from '@/lib/install-progress';
import { useI18n } from '@/lib/i18n';
import InstallJobCard from './InstallJobCard';

type HubJob = InstallJob & {kind?: string; repository?: string; result?: {path: string; commit: string}};
type OfficialPolicy = {id: string; name: string; format: string; robot: string; environment: string; license: string; repository: string; commit: string; version: string};

export default function HubPolicies({onChoose, format}: {onChoose: (path: string) => void; format: string}) {
  const { t } = useI18n();
  const [repo, setRepo] = useState('');
  const [revision, setRevision] = useState('main');
  const [token, setToken] = useState('');
  const [configured, setConfigured] = useState(false);
  const [jobs, setJobs] = useState<HubJob[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [official, setOfficial] = useState<OfficialPolicy[]>([]);
  useEffect(() => {
    storageApi('policy-catalog').then(v => setOfficial(v.policies)).catch(e => setError(e.message));
    storageApi('hub/credential').then(v => setConfigured(v.configured)).catch(e => setError(e.message));
    return watchInstallLog(() => moduleCatalog(), v => setJobs(v.jobs.filter((j: HubJob) => j.kind === 'policy')),
      e => setError((e as Error).message), 1000);
  }, []);
  async function perform(action: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await action(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <section className="space-y-4">
    <h2>{t('Official policies')}</h2>
    <p className="muted">{t('Official policies use fixed versions. Simulation checks do not establish physical robot support.')}</p>
    {official.filter(p => p.format === (format === 'onnx' ? 'onnx-motion' : format)).map(p =>
      <article key={p.id} className="settings-row">
        <div><b>{p.name}</b><p className="muted">{p.robot} · {p.environment} · v{p.version} · {p.license}</p>
          <a href={`https://huggingface.co/${p.repository}/tree/${p.commit}`} target="_blank" rel="noreferrer">{t('Model card')}</a></div>
        <button className="nerv-control action" disabled={busy} onClick={() => perform(() => storageApi('hub/download', {repository: p.repository, revision: p.commit}))}>{t('Download model files')}</button>
      </article>)}
    {!official.some(p => p.format === (format === 'onnx' ? 'onnx-motion' : format)) && <p className="muted">{t('No official policy is listed for this format. You can import your own policy below.')}</p>}
    <h2>{t('Hugging Face policies')}</h2>
    <p className="muted">{t('Download a fixed model revision, then use the same policy checks as a local import. Repository code is never executed.')}</p>
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="form-label">{t('Model repository')}<input className="form-field" placeholder="organization/model" value={repo} onChange={e => setRepo(e.target.value)}/></label>
      <label className="form-label">{t('Revision or commit')}<input className="form-field" value={revision} onChange={e => setRevision(e.target.value)}/></label>
    </div>
    <details><summary>{t('Private model access')}</summary>
      <p className="muted">{t(configured ? 'A token is saved for this NERV channel.' : 'Public models do not need a token.')}</p>
      <label className="form-label">{t('Hugging Face read token')}<input className="form-field" type="password" autoComplete="new-password" value={token} onChange={e => setToken(e.target.value)}/></label>
      <div className="flex gap-3">
        <button className="nerv-control action" disabled={busy || !token.trim()} onClick={() => perform(async () => {await storageApi('hub/credential', {token}); setToken(''); setConfigured(true);})}>{t('Save token')}</button>
        {configured && <button className="nerv-control action" disabled={busy} onClick={() => perform(async () => {await storageApi('hub/credential', {token: null}); setToken(''); setConfigured(false);})}>{t('Remove token')}</button>}
      </div>
    </details>
    <button className="nerv-control action" disabled={busy || !repo.trim() || !revision.trim()} onClick={() => perform(() => storageApi('hub/download', {repository: repo.trim(), revision: revision.trim()}))}>{t('Download model files')}</button>
    {error && <p className="error-text" role="alert">{error}</p>}
    {jobs.map(job => <div key={job.id} className="space-y-2">
      <InstallJobCard job={job} label={job.repository || job.module} retryDisabled={busy}
        onPause={() => perform(() => pauseModuleInstall(job.id))}
        onCancel={() => perform(() => cancelModuleInstall(job.id))}
        onRetry={() => perform(() => resumeModuleInstall(job.id))}/>
      {job.state === 'complete' && job.result && <>
        <p className="text-xs break-all">{t('Pinned commit')}: {job.result.commit}</p>
        <button className="nerv-control action" onClick={() => onChoose(job.result!.path)}>{t('Use downloaded files for import')}</button>
      </>}
    </div>)}
  </section>;
}
