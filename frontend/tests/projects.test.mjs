import {test} from 'node:test';
import assert from 'node:assert/strict';
import {projectRoute,projectForSession,schemeMatchesExecution,projectDate} from '../lib/projects.ts';

test('console is the initial destination and old home links cannot create a session',()=>{
  for (const value of ['', '#', '#new', '#home', '#home/new', '#%broken']) assert.equal(projectRoute(value),'projects');
});
test('project and settings routes survive refresh and escaped links',()=>{
  assert.equal(projectRoute('#project%2Fp_saved'),'project/p_saved');
  assert.equal(projectRoute('#settings'),'settings/general');
  assert.equal(projectRoute('#settings/agents'),'settings/agents');
});
test('return to task resolves only the active run, never an unrelated robot',()=>{
  const projects=[{id:'p1',robot:'so101',active_run:{session_id:'run_a'}},{id:'p2',robot:'so101',active_run:{session_id:'run_b'}}];
  assert.equal(projectForSession(projects,'run_b').id,'p2');
  assert.equal(projectForSession(projects,'old_run'),undefined);
  assert.equal(projectForSession([{id:'p3',robot:'so101'}],'run_a'),undefined);
});

test('execution filters cannot mix external direct control with skills or managed models',()=>{
  assert.equal(schemeMatchesExecution('external_vlm','external_vlm'),true);
  assert.equal(schemeMatchesExecution('external_vlm','fixed_policy'),false);
  assert.equal(schemeMatchesExecution('external_skills','external_vlm'),false);
  assert.equal(schemeMatchesExecution('act','fixed_policy'),true);
  assert.equal(schemeMatchesExecution('managed','policy_group'),true);
  assert.equal(schemeMatchesExecution('future','future'),false);
});

test("Host timestamps preserve seconds and invalid history dates stay unknown",()=>{
  assert.equal(projectDate(1700000000).toISOString(),"2023-11-14T22:13:20.000Z");
  assert.equal(projectDate("2023-11-14T22:13:20Z").toISOString(),"2023-11-14T22:13:20.000Z");
  assert.equal(projectDate(undefined),null);
  assert.equal(projectDate("invalid"),null);
});
