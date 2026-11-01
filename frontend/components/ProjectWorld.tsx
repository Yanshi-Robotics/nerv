"use client";
import { useEffect, useState } from 'react';
import { type Project, type ProjectDetail, getProject, startProject, projectRequest, projectDate, schemeMatchesExecution } from '@/lib/projects';
import { type Registry, type NodeInfo, type SessionSummary, listControlSchemes, type ControlScheme, POLL_NODES_MS } from '@/lib/api';
import { useI18n } from '@/lib/i18n';
import OperatorToolbox from './OperatorToolbox';
import SensingArea from './SensingArea';
import ControlSchemes from './ControlSchemes';
export default function ProjectWorld({project,session,nodes,registry,refresh,onConsole,onProjectChanged}: {
  project:Project;session:SessionSummary|null;nodes:NodeInfo[];registry:Registry|null;refresh:()=>void;onConsole:()=>void;onProjectChanged:(p:Project)=>void;
}){
  const {t}=useI18n();const [detail,setDetail]=useState<ProjectDetail|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[schemes,setSchemes]=useState<ControlScheme[]>([]),[supervision,setSupervision]=useState<any>(null);
  useEffect(()=>{let alive=true;getProject(project.id).then(v=>{if(alive)setDetail(v);}).catch(e=>{if(alive)setError(e.message);});listControlSchemes().then(v=>{if(alive)setSchemes(v.schemes);}).catch(e=>{if(alive)setError(e.message);});return()=>{alive=false;};},[project.id,project.revision,project.active_run?.id]);
  useEffect(()=>{let gone=false,pending=false;const read=async()=>{if(pending)return;pending=true;try{const value=await projectRequest(`projects/${project.id}/control`);if(!gone)setSupervision(value);}catch{if(!gone)setSupervision(null);}finally{pending=false;}};read();const timer=setInterval(read,POLL_NODES_MS);return()=>{gone=true;clearInterval(timer);};},[project.id]);
  const body=nodes.find(n=>n.key===`body:${session?.body}`&&(n.meta.world||null)===(session?.world||null))||null;
  const world=nodes.find(n=>n.key===`world:${session?.world}/${session?.body}`)||null;
  async function start(){setBusy(true);setError('');try{await startProject(project.id);refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function endRun(){if(!project.active_run || !window.confirm(t('End this run? The current task will stop.')))return;setBusy(true);setError('');try{await projectRequest(`projects/${project.id}/runs/${project.active_run.id}/stop`,'POST',{});refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function chooseScheme(scheme:string){setBusy(true);setError('');try{const result=await projectRequest<Project>(`projects/${project.id}`,'PATCH',{scheme,expected_revision:project.revision});onProjectChanged(result);refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <section className="project-world" aria-label={project.name}>
    <header className="project-world-header"><div><strong>{project.name}</strong><span className="environment-badge">{project.mode}</span></div><span className="muted">{project.robot}{project.world?` / ${project.world}`:''}</span><span className="project-supervision" role="status">{t(supervision?.supervision_state==='confirmed'?'Observation acknowledged':supervision?.supervision_state==='expired'?'Supervision expired':'Supervision not confirmed')}{supervision?.last_acknowledged_at&&<small>{new Date(supervision.last_acknowledged_at*1000).toLocaleTimeString()}</small>}</span>{project.active_run&&<button className="nerv-control action" disabled={busy} onClick={endRun}>{t("End run")}</button>}</header>
    {error&&<p role="alert" className="error-text project-error">{error}</p>}
    {session?<OperatorToolbox session={session} bodyNode={body} nodes={nodes} onChanged={refresh} onConsole={onConsole} projectId={project.id} chat={<ControlSchemes session={session} bodyNode={body} brains={registry?.brains||[]} onChanged={refresh} onSetup={onConsole} onModels={onConsole}/>}>
      <SensingArea session={session} bodyNode={body} worldNode={world} robotSensors={registry?.bodies.find(b=>b.name===project.robot)?.sensors} onStateChanged={refresh} project={project} onLayoutSaved={onProjectChanged}/>
    </OperatorToolbox>:<div className="project-standby"><span className="environment-badge">{project.mode}</span><h1>{project.name}</h1><p>{t('Your project is saved. Start a run when the robot, world and execution structure are ready.')}</p>
      <label className="form-label">{t('Control scheme')}<select value={project.scheme||''} className="nerv-input" disabled={busy} onChange={e=>chooseScheme(e.target.value)}><option value="">{t('Choose a control scheme')}</option>{schemes.filter(s=>s.robot===project.robot&&s.mode===project.mode&&schemeMatchesExecution(project.execution,s.template)).map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      {project.readiness?.reasons?.map(reason=><p key={reason} className="muted">{t(reason)}</p>)}
      <div className="project-actions"><button className="nerv-control action" onClick={onConsole}>{t('Open console')}</button><button className="nerv-control nerv-primary action" disabled={busy} onClick={start}>{t(project.mode==='real'?'Open device workspace':'Start world')}</button></div><p className="muted">{t('Opening a workspace does not connect motors or authorize motion.')}</p>
      {!!detail?.runs.length&&<details className="project-history"><summary>{t('Run history')}</summary>{detail.runs.map(run=><div key={run.id}><span>{projectDate(run.created_at)?.toLocaleString()}</span><strong>{t(run.status)}</strong><span>{run.reason}</span></div>)}</details>}
    </div>}
  </section>;
}
