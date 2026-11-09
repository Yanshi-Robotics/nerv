"""Private authentication for policy asset downloads only; no model providers."""
import json
import os
import stat
import tempfile
import threading
from pathlib import Path
_lock = threading.RLock()

def private_write(path: Path, value):
    if path.parent.is_symlink() or path.is_symlink():
        raise PermissionError("private storage must not use symbolic links")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    fd, filename = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(filename, path)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)

class CredentialStore:
    def __init__(self, root: Path):
        self.path = root / "asset-credentials.json"

    def _windows_store(self):
        from keyring.backends.Windows import WinVaultKeyring
        return WinVaultKeyring()

    def _credential_namespace(self):
        # Distinct NERV_HOME directories must not share credentials.
        import hashlib
        return "NERV/" + hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()

    def _read(self):
        if os.name == "nt":
            value = self._windows_store().get_password(self._credential_namespace(), "providers")
            return json.loads(value) if value else {}
        if not self.path.exists():
            return {}
        if (self.path.is_symlink() or not stat.S_ISREG(self.path.stat().st_mode)
                or self.path.stat().st_mode & 0o077):
            raise PermissionError("credential storage must be a private regular file (0600)")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def resolve(self, reference: str):
        if reference != "HUGGINGFACE_TOKEN":
            raise ValueError("Only asset download authentication is supported")
        # Intentionally ignore shell/.env keys. The settings UI owns this store;
        # users re-enter existing keys rather than having them silently imported.
        return self._read().get(reference, "")

    def describe(self, reference: str):
        configured = bool(self._read().get(reference))
        return {"configured": configured, "source": "store" if configured else None,
                "writable": True}

    def check_writable(self):
        """Probe local persistence before a billable request, without writing a key."""
        if os.name == "nt":
            store = self._windows_store()
            import uuid
            probe = "probe-" + uuid.uuid4().hex
            store.set_password(self._credential_namespace(), probe, "write-check")
            store.delete_password(self._credential_namespace(), probe)
            return
        root = self.path.parent
        if root.is_symlink() or self.path.is_symlink():
            raise PermissionError("private storage must not use symbolic links")
        root.mkdir(parents=True, exist_ok=True)
        root.chmod(0o700)
        # Exercise creation and atomic replacement on the same filesystem.
        with tempfile.TemporaryDirectory(prefix=".credential-check-", dir=root) as directory:
            private_write(Path(directory) / "probe.json", {})

    def set(self, reference: str, value: str | None):
        if reference != "HUGGINGFACE_TOKEN":
            raise ValueError("invalid credential reference")
        if value is not None and (not value.strip() or any(ord(c) < 33 or ord(c) > 126 for c in value)):
            raise ValueError("enter a nonempty API key without surrounding whitespace")
        with _lock:
            values = self._read()
            if value is None:
                values.pop(reference, None)
            else:
                values[reference] = value
            if os.name == "nt":
                self._windows_store().set_password(self._credential_namespace(), "providers", json.dumps(values))
            else:
                private_write(self.path, values)
