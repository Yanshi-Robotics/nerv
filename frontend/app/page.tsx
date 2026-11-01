"use client";
import { useCallback, useEffect, useState } from 'react';
import { getRegistry,getNodes,listSessions,userApi,POLL_NODES_MS,type Registry,type NodeInfo,type SessionSummary,type RobotDisplay } from '@/lib/api';
import { listProjects,projectRequest,projectRoute,projectForSession,type Project,type ProjectDraft } from '@/lib/projects';
import ProjectConsole,{emptyProjectDraft} from '@/components/ProjectConsole';
import ProjectWorld from '@/components/ProjectWorld';
import MyRobot from '@/components/MyRobot';
import SettingsPage from '@/components/SettingsPage';
import GlobalTaskBar from '@/components/GlobalTaskBar';
import PolicyLibrary from '@/components/PolicyLibrary';
import AssetLibrary from '@/components/AssetLibrary';
import AgentConnections from '@/components/AgentConnections';
import NavigationIcon from '@/components/NavigationIcon';
import { useI18n } from '@/lib/i18n';
const tabs = [['projects','Projects'],['robots','Robots'],['worlds','Worlds'],['policies','Policies'],['assets','Assets'],['settings/general','Settings']] as const;
export default function Home(){
  const {t}=useI18n();
  const [route,setRoute]=useState('projects'),[registry,setRegistry]=useState<Registry|null>(null),[nodes,setNodes]=useState<NodeInfo[]>([]),[sessions,setSessions]=useState<SessionSummary[]>([]),[projects,setProjects]=useState<Project[]>([]),[robot,setRobot]=useState<RobotDisplay|null>(null),[version,setVersion]=useState(''),[versionError,setVersionError]=useState(false),[error,setError]=useState(''),[currentId,setCurrentId]=useState(''),[consoleOpen,setConsoleOpen]=useState(true);
  const [draft,setDraft]=useState<ProjectDraft>(emptyProjectDraft);
  const refresh=useCallback(async()=>{try{const [r,n,s,p,d]=await Promise.all([getRegistry(),getNodes(),listSessions(),listProjects(),userApi<RobotDisplay>('robot')]);setRegistry(r);setNodes(n);setSessions(s);setProjects(p);setRobot(d);setError('');}catch(e){setError((e as Error).message);}},[]);
  const navigate=useCallback((value:string)=>{value=projectRoute(value);if(value.startsWith('session/')){const p=projectForSession(projects,value.slice(8));value=p?`project/${p.id}`:'settings/diagnostics';}window.location.hash=value;setRoute(value);if(value.startsWith('project/')){setCurrentId(value.slice(8));setConsoleOpen(false);}else setConsoleOpen(true);},[projects]);
  useEffect(()=>{const restore=()=>{const value=projectRoute(window.location.hash);if(window.location.hash!==`#${value}`)window.history.replaceState(null,'',`#${value}`);setRoute(value);if(value.startsWith('project/')){setCurrentId(value.slice(8));setConsoleOpen(false);}else setConsoleOpen(true);};restore();window.addEventListener('hashchange',restore);refresh();userApi('status').then(v=>{if(v.version)setVersion(v.version);else setVersionError(true);}).catch(()=>setVersionError(true));const timer=setInterval(refresh,POLL_NODES_MS);return()=>{window.removeEventListener('hashchange',restore);clearInterval(timer);};},[refresh]);
  useEffect(()=>{const close=(e:KeyboardEvent)=>{if(e.key==='Escape'&&currentId&&consoleOpen)navigate(`project/${currentId}`);};window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);},[currentId,consoleOpen,navigate]);
  useEffect(()=>{const created=(e:Event)=>{const project=(e as CustomEvent<Project>).detail;setProjects(old=>[...old.filter(p=>p.id!==project.id),project]);navigate(`project/${project.id}`);refresh();};window.addEventListener('nerv-project-created',created);return()=>window.removeEventListener('nerv-project-created',created);},[navigate,refresh]);
  const current=projects.find(p=>p.id===currentId)||null;
  const session=sessions.find(s=>s.id===current?.active_run?.session_id)||null;
  const body=nodes.find(n=>n.key===`body:${session?.body}`&&(n.meta.world||null)===(session?.world||null))||null;
  const tabRoute=route.startsWith('project/')?'projects':route;
  async function openProject(id:string){try{await projectRequest(`projects/${id}/open`,'POST',{});navigate(`project/${id}`);refresh();}catch(e){setError((e as Error).message);}}
  async function realProject(){const product=registry?.bodies.find(b=>b.buses.real?.provider);if(!product)return;const existing=projects.find(p=>p.mode==='real'&&p.robot===product.name);if(existing){openProject(existing.id);return;}try{const p=await projectRequest<Project>('projects','POST',{name:robot?.name||product.label,mode:'real',robot:product.name,execution:'external_vlm'});setProjects(old=>[...old,p]);openProject(p.id);}catch(e){setError((e as Error).message);}}
  const updateProject=(p:Project)=>setProjects(old=>old.map(item=>item.id===p.id?{...item,...p}:item));
  const createForWorld=(world:string,bodyName:string)=>{setDraft({...emptyProjectDraft,name:registry?.worlds.find(w=>w.name===world)?.label||world,robot:bodyName,world});navigate('projects');};
  return <main className="project-shell">
    <GlobalTaskBar sessions={sessions} nodes={nodes} navigate={navigate} externalSessionIds={projects.map(p=>p.active_run?.session_id||'')}/>
    {error&&<p role="alert" className="error-text project-error">{t('Unable to refresh local state. Check the NERV service.')} {error}</p>}
    {current&&<ProjectWorld key={current.id} project={current} session={session} nodes={nodes} registry={registry} refresh={refresh} onConsole={()=>navigate('projects')} onProjectChanged={updateProject}/>}
    {!current&&!consoleOpen&&<div className="project-standby"><h1>{t('Project unavailable')}</h1><button className="nerv-control action" onClick={()=>navigate('projects')}>{t('Open console')}</button></div>}
    {consoleOpen&&<section className={`project-console ${current?'project-console-overlay':''}`} aria-label={t('Console')}>
      <header className="console-header"><button className="nerv-control console-brand" onClick={()=>navigate('projects')} aria-label={t('Console')}><img src="/brand/nerv-mark-dark.png" alt=""/>NERV</button><nav aria-label={t('Console')}>{tabs.map(([id,label])=><button key={id} className="nerv-control" aria-current={tabRoute===id||id.startsWith('settings/')&&tabRoute.startsWith('settings/')?'page':undefined} onClick={()=>navigate(id)}>{t(label)}</button>)}</nav>{current&&<button className="nerv-control action" onClick={()=>navigate(`project/${current.id}`)}>{t('Return to world')}</button>}</header>
      <div className="console-scroll">
      {tabRoute==='projects'&&<ProjectConsole projects={projects} registry={registry} draft={draft} setDraft={setDraft} openProject={openProject} navigate={navigate} onCreated={p=>{setProjects(old=>[...old,p]);setDraft(emptyProjectDraft);openProject(p.id);}}/>}
      {tabRoute==='robots'&&<><MyRobot robot={robot} registry={registry} sessions={sessions} onChanged={refresh} navigate={navigate} openWorkspace={()=>realProject()} onProject={realProject}/><section className="project-console-content"><h2>{t('Robot worlds')}</h2><div className="project-list">{projects.map(p=><button key={p.id} className="nerv-control project-list-row" onClick={()=>openProject(p.id)}><NavigationIcon name="robot"/><span>{p.robot}</span><strong>{p.name}</strong><span className="environment-badge">{p.mode}</span><span>{p.world||t('Real workspace')}</span></button>)}</div><h2>{t('Available simulation robots')}</h2>{registry?.bodies.filter(b=>b.buses.sim?.enabled).map(b=><article className="project-list-row" key={b.name}><div><h3>{b.label||b.name}</h3><p className="muted">{registry.worlds.filter(w=>registry.matrix.some(m=>m.body===b.name&&m.world===w.name&&m.ok)).map(w=>w.label||w.name).join(', ')}</p></div><button className="nerv-control action" onClick={()=>{setDraft({...emptyProjectDraft,robot:b.name,name:b.label||b.name});navigate('projects');}}>{t('New project')}</button></article>)}</section></>}
      {tabRoute==='worlds'&&<section className="project-console-content"><header className="project-console-heading"><div><h1>{t('Worlds')}</h1><p>{t('Installed scenes and real workspaces for your projects.')}</p></div><button className="nerv-control action" onClick={()=>navigate('settings/components')}>{t('Install components')}</button></header><div className="project-list">{registry?.worlds.filter(w=>w.kind==='sim').map(w=><article className="project-list-row" key={w.name}><NavigationIcon name="world"/><div className="project-list-name"><h2>{w.label||w.name}</h2><p className="muted">{w.engine}</p></div><span className="environment-badge">Sim</span><button className="nerv-control action" disabled={!registry.matrix.some(m=>m.world===w.name&&m.ok)} onClick={()=>createForWorld(w.name,registry.matrix.find(m=>m.world===w.name&&m.ok)?.body||'')}>{t('Create project')}</button></article>)}</div>{!registry?.worlds.length&&<p className="project-empty">{t('Install the simulation component first.')}</p>}<h2>{t('Real workspaces')}</h2><p className="muted">{t('Real workspaces contain device and camera configuration, not a reconstructed digital twin.')}</p>{projects.filter(p=>p.mode==='real').map(p=><button key={p.id} className="nerv-control project-list-row" onClick={()=>openProject(p.id)}>{p.name}<span className="environment-badge">Real</span></button>)}</section>}
      {tabRoute==='policies'&&<PolicyLibrary onComponents={()=>navigate('settings/components')}/>}
      {tabRoute==='assets'&&<AssetLibrary/>}
      {tabRoute==='settings/agents'&&<><div className="project-console-content"><button className="nerv-control action" onClick={()=>navigate('settings/general')}>{t('Settings')}</button></div><AgentConnections projects={projects}/></>}
      {tabRoute.startsWith('settings/')&&tabRoute!=='settings/agents'&&<SettingsPage section={tabRoute.slice(9)} registry={registry} session={session} bodyNode={body} version={version} versionError={versionError} navigate={navigate} onChanged={refresh}/>}
      </div>
    </section>}
  </main>;
}
