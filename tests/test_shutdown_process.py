"""Exercise OS signals, idle SSE and owned-child cleanup without hardware or models."""
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]


def wait_log(path, text, process, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        content = path.read_text()
        if text in content:
            return content
        if process.poll() is not None:
            pytest.fail(content)
        time.sleep(0.02)
    pytest.fail(path.read_text())


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process-group signals; Windows console requires a real console')
@pytest.mark.parametrize('wrapper,interrupts', [('python', 1), ('python', 2), ('python', 0), ('node', 2), ('node-fallback', 2)])
def test_terminal_shutdown_drains_sse_and_reaps_child(tmp_path, wrapper, interrupts):
    if wrapper != 'python' and not shutil.which('node'):
        pytest.skip('Node.js unavailable')
    script = tmp_path / 'serve.py'
    script.write_text('''import asyncio, subprocess, sys
import uvicorn
from nerv.platform.server import app, nerv, request_shutdown
from nerv.platform.serving import BrowserServer
from nerv.platform.launcher import NodeHandle
child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); time.sleep(120)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
nerv.launcher.nodes['fixture'] = NodeHandle('tool', 'fixture', '', proc=child)
print('CHILD=' + str(child.pid), flush=True)
session, _ = nerv.store.new('operator', None, None, [])
print('SESSION=' + session.id, flush=True)
async def finish():
    print('CLEANING', flush=True)
    await asyncio.sleep(0.3)
    print('CLEANED', flush=True)
app.on_event('shutdown')(finish)
BrowserServer(uvicorn.Config(app, host='127.0.0.1', port=0, timeout_graceful_shutdown=2), open_browser=False, before_shutdown=request_shutdown).run()
print('RETURNED', flush=True)
''')
    command = [sys.executable, str(script)]
    if wrapper != 'python':
        code = ('process.execve = undefined; ' if wrapper == 'node-fallback' else '')
        bootstrap = tmp_path / 'home/bootstrap'
        bootstrap.mkdir(parents=True)
        (bootstrap / 'launch.py').write_text(script.read_text())
        (tmp_path / 'home/active.json').write_text(json.dumps({'python': sys.executable}))
        code += f'import {{main}} from {json.dumps((ROOT / "launcher/nerv.mjs").as_uri())}; '
        code += 'await main([]);'
        command = ['node', '--input-type=module', '-e', code]
    log = tmp_path / 'server.log'
    connections = []
    child_pid = None
    with log.open('w') as output:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True, env={**os.environ, 'NERV_HOME': str(tmp_path / 'home')})
        try:
            content = wait_log(log, 'NERV interface:', process)
            child_pid = int(re.search(r'CHILD=(\d+)', content)[1])
            port = int(re.search(r'NERV interface: http://127.0.0.1:(\d+)/', content)[1])
            sid = re.search(r'SESSION=(s_\d+)', content)[1]
            for endpoint in ('/api/nerv/events', f'/api/sessions/{sid}/robot-events'):
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                connections.append(connection)
                connection.request('GET', endpoint)
                assert connection.getresponse().status == 200
            os.killpg(process.pid, signal.SIGINT if interrupts else signal.SIGTERM)
            wait_log(log, 'CLEANING', process)
            if interrupts == 2:
                os.killpg(process.pid, signal.SIGINT)
            assert process.wait(timeout=10) == 0, log.read_text()
            content = log.read_text()
            assert content.count('CLEANED') == 1 and 'RETURNED' in content
            assert 'Traceback' not in content and 'CancelledError' not in content
            assert 'NERV: ' not in content
            with pytest.raises(ProcessLookupError):
                os.kill(child_pid, 0)
        finally:
            for connection in connections:
                connection.close()
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            if child_pid:
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
