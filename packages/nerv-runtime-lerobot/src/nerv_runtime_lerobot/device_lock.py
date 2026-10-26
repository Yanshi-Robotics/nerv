"""One serial-device owner across NERV homes belonging to this OS user."""
import hashlib
import os
from pathlib import Path
import tempfile
from filelock import FileLock


def device_lock(port):
    user = str(Path.home().resolve())
    root = Path(tempfile.gettempdir()) / ('nerv-devices-' + hashlib.sha256(user.encode()).hexdigest()[:16])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    identity = os.path.normcase(os.path.realpath(port))
    return FileLock(root / (hashlib.sha256(identity.encode()).hexdigest() + '.lock'), timeout=0)
