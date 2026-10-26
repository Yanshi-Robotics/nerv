"""Versioned operator prerequisites. Never configure, calibrate or energize motors."""
from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import re
import sys
import time

from .standard import MOTOR_IDS, LEROBOT_VERSION, validate_calibration

CAPABILITY = {'version': 1, 'module': 'nerv_runtime_lerobot.prerequisites',
              'remediation': 'external_manual', 'guide': 'https://huggingface.co/docs/lerobot/so101'}


def item(state, code, evidence=None):
    return {'state': state, 'code': code, 'evidence': evidence, 'remediation': 'external_manual'}


def calibration(payload):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', payload.get('id', '')):
        raise ValueError('Select the robot ID used for calibration')
    path = Path(payload['calibration_dir']).expanduser().resolve() / (payload['id'] + '.json')
    value = json.loads(path.read_text(encoding='utf-8'))
    validate_calibration(value)
    return path, value


def context(payload):
    """Passive evidence only. A matching file is never a device check."""
    from serial.tools import list_ports
    path, _ = calibration(payload)
    port = payload['port']
    identity = next(({'serial': p.serial_number, 'vid': p.vid, 'pid': p.pid, 'location': p.location}
                     for p in list_ports.comports() if (p.device.upper() == port.upper() if sys.platform == 'win32'
                         else Path(p.device).resolve() == Path(port).resolve())), None)
    receipt = Path(sys.executable).parent.parent / 'nerv-install.json'
    value = {'port': str(Path(port).resolve()) if sys.platform != 'win32' else port.upper(),
             'identity': identity, 'id': payload['id'], 'calibration_path': str(path),
             'calibration_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
             'environment': hashlib.sha256(receipt.read_bytes()).hexdigest() if receipt.exists() else None,
             'adapter': version('nerv-runtime-lerobot'), 'python': sys.executable}
    return {'fingerprint': hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest(), **value}


def status(payload, home):
    try:
        current = context(payload)
        target = Path(home) / 'devices/prerequisites.json'
        previous = json.loads(target.read_text()) if target.exists() else {}
        if current.get('identity', True) is None:
            return {'state': 'unknown', 'code': 'device_changed', 'context': current}
        if previous.get('context') == current and previous.get('state') == 'passed':
            return previous
        return {'state': 'unknown', 'code': 'check_required', 'context': current}
    except FileNotFoundError:
        return {'state': 'unmet', 'code': 'calibration_missing'}
    except (ValueError, KeyError, TypeError):
        return {'state': 'unmet', 'code': 'calibration_invalid'}
    except OSError:
        return {'state': 'unknown', 'code': 'calibration_unreadable'}


def require_current(payload, home):
    result = status(payload, home)
    if result['state'] != 'passed':
        raise ValueError('Device prerequisites need checking in robot setup: ' + result['code'])
    return result


def inference_device():
    receipt = Path(sys.executable).parent.parent / 'nerv-install.json'
    value = json.loads(receipt.read_text()) if receipt.exists() else {}
    backend = value.get('platform', {}).get('backend')
    if backend not in ('cpu', 'cu130'):
        raise ValueError('Install and verify a managed CPU or NVIDIA runtime before setup')
    return 'cuda' if backend == 'cu130' else 'cpu'


def native_bus(port, values):
    if version('lerobot') != LEROBOT_VERSION:
        raise ValueError('Upgrade the adapter to its supported LeRobot version')
    from lerobot.motors import Motor, MotorCalibration, MotorNormMode
    from lerobot.motors.feetech import FeetechMotorsBus
    return FeetechMotorsBus(port, motors={name: Motor(mid, 'sts3215', MotorNormMode.DEGREES)
                                          for name, mid in MOTOR_IDS.items()},
                           calibration={name: MotorCalibration(**v) for name, v in values.items()})


def inspect_bus(bus, values):
    """Use the native read API only; no robot.connect(), handshakes or writes."""
    bus.connect(handshake=False)
    found = {name: bus.ping(mid) for name, mid in MOTOR_IDS.items()}
    # No answer is inconclusive: unplugging and bad configuration look alike.
    if any(v is None for v in found.values()):
        return item('unknown', 'motors_unreachable', found), item('unknown', 'check_incomplete')
    expected = bus.model_number_table['sts3215']
    if any(v != expected for v in found.values()):
        return item('unmet', 'motor_model_mismatch', found), item('unknown', 'check_incomplete')
    modes = {name: bus.read('Operating_Mode', name, normalize=False) for name in MOTOR_IDS}
    if any(mode != 0 for mode in modes.values()):
        return item('unmet', 'motor_model_mismatch', modes), item('unknown', 'check_incomplete')
    actual = {name: asdict(v) for name, v in bus.read_calibration().items()}
    # LeRobot's native Feetech read supplies drive_mode=0 for this product.
    matches = actual == values
    return item('passed', 'motor_setup_passed', found), item(
        'passed' if matches else 'unmet', 'calibration_matches' if matches else 'calibration_mismatch', actual)


def check(payload, home, *, factory=native_bus):
    from .device_lock import device_lock
    from .manage import _write
    from filelock import Timeout
    checks = {k: item('unknown', 'check_incomplete') for k in ('calibration_file', 'motors', 'device_calibration')}
    result = {'version': 1, 'state': 'unknown', 'checks': checks, 'checked_at': time.time(),
              'capability': CAPABILITY, 'hardware_verified': False}
    bus = None
    try:
        _, values = calibration(payload)
        checks['calibration_file'] = item('passed', 'calibration_file_valid')
        result['context'] = context(payload)
        with device_lock(payload['port']):
            try:
                bus = factory(payload['port'], values)
                checks['motors'], checks['device_calibration'] = inspect_bus(bus, values)
            finally:
                if bus is not None:
                    # Close the serial handle directly even after a partial open.
                    # Native disconnect() defaults to a torque write; never call it.
                    bus.port_handler.closePort()
        if context(payload) != result['context'] or result['context'].get('identity', True) is None:
            checks['device_calibration'] = item('unknown', 'device_changed')
    except FileNotFoundError:
        if checks['calibration_file']['state'] == 'passed':
            checks['motors'] = item('unknown', 'device_unreadable')
        else:
            checks['calibration_file'] = item('unmet', 'calibration_missing')
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as error:
        key = 'calibration_file' if checks['calibration_file']['state'] != 'passed' else 'motors'
        checks[key] = item('unmet' if key == 'calibration_file' else 'unknown', 'calibration_invalid' if key == 'calibration_file' else 'check_incomplete', str(error))
    except Timeout:
        checks['motors'] = item('unknown', 'device_busy')
    except Exception as error:
        checks['motors'] = item('unknown', 'device_unreadable', type(error).__name__)
    states = [v['state'] for v in checks.values()]
    result['state'] = 'passed' if all(s == 'passed' for s in states) else 'unmet' if 'unmet' in states else 'unknown'
    _write(Path(home) / 'devices/prerequisites.json', result)
    return result


def main():
    import os
    # A crashed Host must not leave a serial probe alive indefinitely.
    import threading
    parent = os.getppid()
    def watchdog():
        deadline = time.monotonic() + 65
        while time.monotonic() < deadline and os.getppid() == parent:
            time.sleep(0.5)
        os._exit(2)  # OS closes the serial handle; no torque command.
    threading.Thread(target=watchdog, daemon=True).start()
    payload = json.loads(sys.stdin.readline())
    print(json.dumps(check(payload, os.environ['NERV_HOME'])), flush=True)


if __name__ == '__main__':
    main()
