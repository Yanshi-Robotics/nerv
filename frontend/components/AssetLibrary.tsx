"use client";
import { useEffect, useState } from 'react';
import { storageApi } from '@/lib/api';
import { useI18n } from '@/lib/i18n';

type Asset = {id: string; name: string; workspace?: string; current: string;
  origin: string; revisions: string[]; validation: {runtime: string}};

export default function AssetLibrary() {
  const { t } = useI18n();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [source, setSource] = useState('');
  const [destination, setDestination] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const refresh = () => storageApi('assets').then(v => setAssets(v.assets));
  useEffect(() => { refresh().catch(e => setError(e.message)); }, []);
  async function perform(path: string, body: Record<string, unknown>) {
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await storageApi(path, body);
      setMessage(result.path || t('Asset files checked. Runtime and task validation are separate.'));
      await refresh();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <section className="settings-content space-y-5">
    <h1>{t('My assets')}</h1>
    <p className="muted">{t('Personal copies keep your changes separate from official updates. Save a revision after editing its files.')}</p>
    {error && <p role="alert" className="error-text">{error}</p>}
    {message && <p role="status" className="break-all">{message}</p>}
    <h2>{t('Saved assets')}</h2>
    {!assets.length && <p className="muted">{t('No local assets yet.')}</p>}
    {assets.map(asset => <article key={asset.id} className="rounded-xl border border-[var(--border)] p-4 space-y-3">
      <h3>{asset.name}</h3>
      <p className="muted">{t('Saved revisions')}: {asset.revisions.length}</p>
      <details><summary>{t('Files and revision')}</summary>
        <p className="break-all text-sm">{asset.workspace}</p>
        <code className="break-all text-xs">{asset.current}</code>
      </details>
      <p className="muted text-sm">{t('Asset files checked. Runtime and task validation are separate.')}</p>
      <div className="flex flex-wrap gap-2">
        <button className="nerv-control action" disabled={busy} onClick={() => perform(`assets/${asset.id}/clone`, {})}>{t('Create personal copy')}</button>
        {asset.origin !== 'official' && <button className="nerv-control action" disabled={busy} onClick={() => perform(`assets/${asset.id}/save`, {})}>{t('Check and save revision')}</button>}
        <button className="nerv-control action" disabled={busy} onClick={() => perform(`assets/${asset.id}/verify`, {})}>{t('Check')}</button>
      </div>
      <details><summary>{t('Export asset package')}</summary>
        <label className="form-label">{t('New ZIP file path')}<input className="form-field" value={destination} onChange={e => setDestination(e.target.value)} /></label>
        <button className="nerv-control action" disabled={busy || !destination.trim()} onClick={() => perform(`assets/${asset.id}/export`, {destination})}>{t('Export asset package')}</button>
      </details>
    </article>)}
    <details open={!assets.length}><summary>{t('Import local asset')}</summary>
      <p className="muted">{t('Choose a directory or ZIP containing nerv-asset.json and its MuJoCo files. The original stays unchanged.')}</p>
      <label className="form-label">{t('Asset directory or ZIP path')}<input className="form-field" value={source} onChange={e => setSource(e.target.value)} /></label>
      <button className="nerv-control action" disabled={busy || !source.trim()} onClick={() => perform('assets/import', {source})}>{busy ? t('Working…') : t('Import local asset')}</button>
    </details>
  </section>;
}
