"""Installed and source launches share user storage; immutable assets stay with code."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_source_checkout_respects_selected_data_home(tmp_path):
    selected = tmp_path / "中文 home"
    script = "from nerv import paths; import json; print(json.dumps({k:getattr(paths,k) for k in ('NERV_HOME','DATA_ROOT','MEMORY_DIR','LOGS_DIR','ENV_FILE','REGISTRY_ROOT')}))"
    result = subprocess.run([sys.executable, "-c", script], env={**os.environ, "NERV_HOME": str(selected)}, capture_output=True, text=True, check=True)
    values = json.loads(result.stdout)
    assert values["DATA_ROOT"] == values["NERV_HOME"] == str(selected)
    for key in ("MEMORY_DIR", "LOGS_DIR", "ENV_FILE"):
        assert Path(values[key]).is_relative_to(selected)
    assert not Path(values["REGISTRY_ROOT"]).is_relative_to(selected)
    assert not selected.exists()  # importing paths does not initialize or migrate data


def test_runtime_discovery_filters_missing_and_malformed_installations(tmp_path, monkeypatch):
    from nerv import paths
    from nerv.platform.runtime_management import installations
    monkeypatch.setattr(paths, "NERV_HOME", str(tmp_path))
    folder = tmp_path / "runtimes"
    folder.mkdir()
    good = {"provider": "example", "python": sys.executable, "label": "Example"}
    (folder / "good.json").write_text(json.dumps(good))
    (folder / "bad.json").write_text("not json")
    (folder / "list.json").write_text("[]")
    (folder / "missing.json").write_text(json.dumps({**good, "python": str(tmp_path / "missing")}))
    assert installations() == [good]
