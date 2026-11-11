"use client";
import {useEffect, useState} from 'react';
import {type Project, type WorldInstance, type RobotResource, projectRequest} from '@/lib/projects';
import {useI18n} from '@/lib/i18n';
export default function SharedWorldSetup({projects,onCreated}:{projects:Project[];onCreated:()=>void}) {
  const {t}=useI18n();
  const [template,setTemplate]=useState(''),[capacity,setCapacity]=useState(2),[allow,setAllow]=useState(false);
  const [worlds,setWorlds]=useState<WorldInstance[]>([]),[robots,setRobots]=useState<RobotResource[]>([]);
  const [error,setError]=useState(''),[busy,setBusy]=useState(false);
  async function refresh(){const [w,r]=await Promise.all([projectRequest('world-instances'),projectRequest('robot-instances')]);setWorlds(w);setRobots(r);}
  useEffect(()=>{refresh().catch(e=>setError(e.message));},[]);
  async function perform(fn:()=>Promise<unknown>){setBusy(true);setError('');try{await fn();await refresh();onCreated();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  const eligible=projects.filter(p=>p.mode==='sim'&&p.world);
  return <section className="project-configuration"><h2>{t('Persistent worlds')}</h2><p className="muted">{t('Create an empty world first. Ending a run keeps its robot in the world. Removing a robot is a separate operation.')}</p>
    <label className="form-label">{t('Scene and robot template')}<select className="nerv-input" value={template} onChange={e=>setTemplate(e.target.value)}><option value="">{t('Choose a project')}</option>{eligible.map(p=><option key={p.id} value={p.id}>{p.name} · {p.world} / {p.robot}</option>)}</select></label>
    <label className="form-label">{t('Maximum robots')}<input className="nerv-input" type="number" min="1" step="1" value={capacity} onChange={e=>setCapacity(Number(e.target.value))}/></label>
    <label className="pairing-project"><input type="checkbox" checked={allow} onChange={e=>setAllow(e.target.checked)}/>{t('Allow granted Agents to request topology changes')}</label><p className="muted">{t('Adding or removing a robot stops all members briefly. Every controller must observe again and acquire a new lease.')}</p>
    <button className="nerv-control action" disabled={busy||!template||!Number.isInteger(capacity)||capacity<1} onClick={()=>perform(async()=>{const p=eligible.find(p=>p.id===template)!;return projectRequest('world-instances','POST',{scene:p.world,robot_profile:p.robot,topology_policy:{allow_agent_changes:allow,max_robots:capacity}});})}>{t('Create empty world')}</button>
    <button className="nerv-control action" disabled={busy} onClick={()=>perform(refresh)}>{t('Refresh')}</button>
    {worlds.map(w=><article className="project-list-row" key={w.world_instance_id}><div><h3>{w.world} · {t(w.status)}</h3><code>{w.world_instance_id}</code><p>{t('Topology revision')}: {w.topology_revision||1} · {t('Robot')}: {w.roster.length}</p>
      {w.roster.map(slot=>{const r=robots.find(r=>r.robot_instance_id===slot.robot_instance_id);return <div key={slot.robot_instance_id}><a href={`#project/${slot.project_id}`}>{slot.project_name||projects.find(p=>p.id===slot.project_id)?.name||slot.project_id}</a><dl className="instance-bindings"><div><dt>{t('Robot')}</dt><dd>{slot.robot_instance_id}</dd></div><div><dt>{t('Brain')}</dt><dd>{r?.brain_instance_id}</dd></div><div><dt>{t('Status')}</dt><dd>{t(r?.status||'offline')}</dd></div></dl><button className="nerv-control action" disabled={busy||!!projects.find(p=>p.id===slot.project_id)?.active_run} onClick={()=>{if(window.confirm(t('Remove this simulation robot? Other members will stop during the topology change.')))perform(()=>projectRequest(`robot-instances/${slot.robot_instance_id}/remove`,'POST',{}));}}>{t('Remove simulation robot')}</button></div>;})}
      <div className="project-actions"><button className="nerv-control action" disabled={busy||!['configured','interrupted','stopped'].includes(w.status)} onClick={()=>perform(()=>projectRequest(`world-instances/${w.world_instance_id}/start`,'POST',{}))}>{t('Start world')}</button><button className="nerv-control action" disabled={busy||w.status==='stopped'} onClick={()=>{if(window.confirm(t('Stop the shared world and all its robots?')))perform(()=>projectRequest(`world-instances/${w.world_instance_id}/stop`,'POST',{}));}}>{t('Stop shared world')}</button></div></div></article>)}
    {error&&<p role="alert" className="error-text">{error}</p>}
  </section>;
}
