"use client";
import {useEffect, useState} from 'react';
import {projectRequest, type RobotResource} from '@/lib/projects';
import {POLL_NODES_MS} from '@/lib/api';
import {useI18n} from '@/lib/i18n';

type Reading={connected:boolean;resource_status:string;robot_epoch?:string;delivered_at?:number;
  state?:{frame_seq?:number};images?:{name:string;mimeType:string;data:string}[]};

/** Resource monitoring never starts a Run, acknowledges a frame or acquires control. */
export default function ResourceObservation({resource,inactive}:{resource:RobotResource;inactive:boolean}){
  const {t}=useI18n();
  const [reading,setReading]=useState<Reading|null>(null),[error,setError]=useState('');
  useEffect(()=>{
    let gone=false,pending=false;setReading(null);setError('');
    if(inactive)return;
    const read=async()=>{
      if(pending)return;pending=true;
      try{const value=await projectRequest<Reading>(`robot-instances/${resource.robot_instance_id}/observation`);if(!gone){setReading(value);setError('');}}
      catch(e){if(!gone){setReading(null);setError((e as Error).message);}}
      finally{pending=false;}
    };
    read();const timer=setInterval(read,POLL_NODES_MS);
    return()=>{gone=true;clearInterval(timer);};
  },[resource.robot_instance_id,resource.status,inactive]);
  return <section aria-label={t('Robot observation')} className="resource-observation">
    <header><strong>{t('Robot observation')}</strong><span className="environment-badge">{t(reading?.connected?'Connected':'Offline')}</span></header>
    <p className="muted">{t('Observation does not grant motion control.')}</p>
    {error&&<p role="alert" className="error-text">{error}</p>}
    {reading?.delivered_at&&<p className="muted">{new Date(reading.delivered_at*1000).toLocaleTimeString()} · {t('Frame')} {reading.state?.frame_seq??'—'}</p>}
    <div className="resource-observation-images">{reading?.images?.map(i=><figure key={i.name}><img src={`data:${i.mimeType};base64,${i.data}`} alt={i.name}/><figcaption>{i.name}</figcaption></figure>)}</div>
  </section>;
}
