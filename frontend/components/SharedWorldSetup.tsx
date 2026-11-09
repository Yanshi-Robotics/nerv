"use client";
import {useState} from 'react';
import {type Project, projectRequest} from '@/lib/projects';
import {useI18n} from '@/lib/i18n';
export default function SharedWorldSetup({projects,onCreated}:{projects:Project[];onCreated:()=>void}) {
  const {t}=useI18n();
  const [slots,setSlots]=useState([{project_id:'',x:'',y:'',z:'',yaw:''},{project_id:'',x:'',y:'',z:'',yaw:''}]);
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[created,setCreated]=useState('');
  const eligible=projects.filter(p=>p.mode==='sim'&&p.active_run?.status==='reserved'&&!p.active_run?.shared_world);
  async function compose(){setBusy(true);setError('');try{
    if(slots.some(s=>!s.project_id||[s.x,s.y,s.z,s.yaw].some(v=>v.trim()===''||!Number.isFinite(Number(v)))))throw new Error(t('Choose two allocated projects and enter their spawn poses.'));
    const result=await projectRequest('world-instances','POST',{robots:slots.map(s=>({project_id:s.project_id,spawn:{x:Number(s.x),y:Number(s.y),z:Number(s.z),yaw:Number(s.yaw)}}))});
    setCreated(result.world_instance_id);onCreated();
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <details className="project-configuration"><summary>{t('Compose a shared world')}</summary><p className="muted">{t('Allocate a Brain for each project first. Robot composition and spawn poses are fixed until this world ends.')}</p>
    {slots.map((slot,i)=><fieldset key={i}><legend>{t('Robot')} {i+1}</legend><select className="nerv-input" aria-label={`${t('Project')} ${i+1}`} value={slot.project_id} onChange={e=>setSlots(slots.map((s,j)=>j===i?{...s,project_id:e.target.value}:s))}><option value="">{t('Choose a project')}</option>{eligible.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select><div className="project-form-grid">{(['x','y','z','yaw'] as const).map(key=><label className="form-label" key={key}>{key==='yaw'?t('Heading (radians)'):`${key} (m)`}<input className="nerv-input" type="number" step="any" value={slot[key]} onChange={e=>setSlots(slots.map((s,j)=>j===i?{...s,[key]:e.target.value}:s))}/></label>)}</div></fieldset>)}
    <button className="nerv-control action" disabled={busy} onClick={compose}>{t('Freeze world composition')}</button>{created&&<p role="status">{t('World configured')}: {created}</p>}{error&&<p role="alert" className="error-text">{error}</p>}
  </details>;
}
