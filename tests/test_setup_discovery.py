"""Offline filesystem and camera identity tests; never open camera devices."""
from types import SimpleNamespace
import sys

import pytest

from nerv.platform.local_files import directories
from nerv_runtime_lerobot import discovery as d


def test_directory_paging_unicode_and_no_file_contents(tmp_path):
    for name in ('学生 policy', '.hidden'):
        (tmp_path / name).mkdir()
    (tmp_path / 'secret.json').write_text('never returned')
    result = directories(tmp_path)
    assert result['directories'] == [{'name': '学生 policy', 'path': str(tmp_path / '学生 policy')}]
    assert 'never returned' not in str(result)
    with pytest.raises(ValueError):
        directories(tmp_path / 'secret.json')


@pytest.mark.parametrize('platform,backend', [('win32', 700), ('darwin', 1200)])
def test_native_enumeration_uses_explicit_backend_without_capture(monkeypatch, platform, backend):
    monkeypatch.setattr(d.sys, 'platform', platform)
    monkeypatch.setitem(sys.modules, 'cv2', SimpleNamespace(CAP_DSHOW=700, CAP_AVFOUNDATION=1200))
    seen = []
    def enumerate_cameras(value):
        seen.append(value)
        return [SimpleNamespace(index=2, name='USB camera', path='unique', backend=value)]
    monkeypatch.setitem(sys.modules, 'cv2_enumerate_cameras', SimpleNamespace(enumerate_cameras=enumerate_cameras))
    assert d.camera_candidates() == [{'device': 2, 'label': 'USB camera', 'stable': True, 'identity': 'unique', 'backend': backend}]
    assert seen == [backend]


def test_camera_identity_survives_index_change_and_rejects_missing_or_ambiguous(monkeypatch):
    cameras = {'top': {'index_or_path': 0}, 'wrist': {'index_or_path': 1}}
    candidates = [{'device': 3, 'identity': 'top', 'backend': 700}, {'device': 0, 'identity': 'wrist', 'backend': 700}]
    monkeypatch.setattr(d, 'camera_candidates', lambda: candidates)
    result = d.resolve_cameras(cameras, {'top': 'top', 'wrist': 'wrist'})
    assert result['top']['index_or_path'] == 3
    assert result['wrist']['index_or_path'] == 0
    assert cameras['top']['index_or_path'] == 0
    candidates.append(candidates[0])
    with pytest.raises(ValueError, match='ambiguous'):
        d.resolve_cameras(cameras, {'top': 'top'})
    with pytest.raises(ValueError, match='missing'):
        d.resolve_cameras(cameras, {'top': 'absent'})


def test_calibration_picker_checks_motor_ids_and_preserves_original(tmp_path):
    import json
    from nerv_runtime_lerobot.manage import manage
    from nerv_runtime_lerobot.standard import MOTOR_IDS
    calibration = {name: {'id': motor_id, 'drive_mode': 0, 'homing_offset': 0,
                         'range_min': 1, 'range_max': 4000} for name, motor_id in MOTOR_IDS.items()}
    target = tmp_path / 'student.json'
    target.write_text(json.dumps(calibration))
    original = target.read_bytes()
    args = {'id': 'student', 'calibration_dir': str(tmp_path)}
    assert manage('calibrations', {'directory': str(tmp_path)}, tmp_path)['calibrations'][0]['id'] == 'student'
    assert manage('validate_calibration', args, tmp_path)['connected'] is False
    assert target.read_bytes() == original
    calibration['elbow_flex']['id'] = 99
    target.write_text(json.dumps(calibration))
    with pytest.raises(ValueError, match='standard SO-101 motor'):
        manage('validate_calibration', args, tmp_path)
    with pytest.raises(ValueError, match='Available IDs: student'):
        manage('validate_calibration', {**args, 'id': 'missing'}, tmp_path)
