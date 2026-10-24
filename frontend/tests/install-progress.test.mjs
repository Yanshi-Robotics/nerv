import {test} from 'node:test';
import assert from 'node:assert/strict';
import {downloadPercent, watchInstallLog} from '../lib/install-progress.ts';
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const download = {state: 'running', phase: 'download', downloaded: 50, download_total: 100};

test('percent is only for a measurable active file, not stale dependency progress', () => {
  assert.equal(downloadPercent(download), 50);
  for (const patch of [{phase:'dependencies'}, {state:'failed'}, {download_total:null},
    {download_total:0}, {downloaded:null}, {downloaded:-1}, {downloaded:NaN}, {download_total:Infinity}]) {
    assert.equal(downloadPercent({...download,...patch}), undefined);
  }
  assert.equal(downloadPercent({...download,downloaded:101}),100);
});

test('live log updates without clicks, serializes reads and recovers after a failure', async () => {
  let calls=0, pending=0, maxPending=0, failures=0;
  const received=[];
  const stop=watchInstallLog(async () => {
    maxPending=Math.max(maxPending,++pending);
    const call=++calls;
    await wait(5); pending--;
    if(call===2) throw new Error('offline');
    return `line ${call}`;
  }, value=>received.push(value), ()=>failures++, 1);
  try {
    for(let i=0;i<100 && received.length<3;i++) await wait(5);
    assert.equal(maxPending,1);
    assert.equal(failures,1);
    assert.ok(received.length>=3);
    assert.equal(received[0],'line 1');
    assert.equal(received[1],'line 3');
  } finally {stop();}
});

test('unmount aborts requests and ignores late replies', async () => {
  let resolve, signal, received=0;
  const stop=watchInstallLog(s=>{signal=s;return new Promise(r=>resolve=r);},()=>received++,()=>{},1);
  stop(); resolve('late'); await wait(5);
  assert.equal(signal.aborted,true);
  assert.equal(received,0);
});

test('finished jobs fetch one final snapshot without continued polling', async () => {
  let count=0;
  const stop=watchInstallLog(async()=>++count,()=>{},()=>{},1,false);
  await wait(10); stop();
  assert.equal(count,1);
});
