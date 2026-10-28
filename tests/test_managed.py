from nerv.managed import channel_home, environment_identity, lease, active_leases, storage_root


def test_channels_share_storage_not_data(tmp_path, monkeypatch):
    monkeypatch.delenv('NERV_STORAGE_HOME', raising=False)
    assert storage_root(tmp_path / 'profiles/preview') == tmp_path
    assert channel_home('preview', tmp_path) != channel_home('stable', tmp_path)


def test_identity_ignores_release_and_url_but_not_compute():
    spec = {'packages': ['a.whl']}
    plan = {'target': 'linux-x86_64', 'backend': 'cpu', 'artifacts': [
        {'name': 'a.whl', 'sha256': 'a' * 64, 'kind': 'wheel'}]}
    first = environment_identity('example', spec, {}, plan)
    plan.update(release_version='next', base_url='https://different.example')
    assert environment_identity('example', spec, {}, plan) == first
    plan['backend'] = 'cu130'
    assert environment_identity('example', spec, {}, plan) != first


def test_lease_is_released_after_exception(tmp_path):
    path = tmp_path / 'environment'
    try:
        with lease(tmp_path, [path]):
            assert active_leases(tmp_path) == [str(path)]
            raise RuntimeError('worker failed')
    except RuntimeError:
        pass
    assert active_leases(tmp_path) == []


def test_inventory_preserves_legacy_preview_and_interrupted_update(tmp_path):
    import json
    from nerv.managed import environments
    from nerv.install_system import atomic_json
    preview = tmp_path / 'profiles/preview'
    directory = preview / 'envs/host/old'
    python = directory / 'bin/python'
    python.parent.mkdir(parents=True)
    python.touch()
    record = {'python': str(python), 'version': 'old'}
    atomic_json(directory / 'nerv-install.json', record)
    snapshot = tmp_path / 'snapshots/recovery'
    atomic_json(snapshot / 'runtimes/example.json', record)
    atomic_json(preview / 'update-pending.json', {'snapshot': str(snapshot)})
    rows = environments(tmp_path)
    assert len(rows) == 1
    assert rows[0]['references'] == ['preview:rollback']
    assert rows[0]['health'] == 'needs_check'
    assert json.loads((directory / 'nerv-install.json').read_text()) == record
