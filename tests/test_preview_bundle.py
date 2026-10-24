"""A candidate preview must expose verified local modules without host activation."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "prepare_preview_bundle", Path(__file__).parents[1] / "scripts/prepare_preview_bundle.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def test_local_catalogue_and_cache_preserve_preview_data(tmp_path, monkeypatch):
    bundle, home = tmp_path / "bundle", tmp_path / "home"
    bundle.mkdir()
    home.mkdir()
    content = b"offline fixture asset"
    (bundle / "fixture.whl").write_bytes(content)
    manifest = {"schema": 1, "version": "test", "products": {
        "host": {}, "fixture-sim": {"label": "Fixture", "files": ["fixture.whl"]}},
        "files": {"fixture.whl": {"sha256": hashlib.sha256(content).hexdigest()}}}
    (bundle / "release.json").write_text(json.dumps(manifest))
    (home / "credentials.json").write_text("private fixture")
    assert helper.prepare(bundle, home) == ["fixture-sim"]
    assert helper.prepare(bundle, home) == ["fixture-sim"]
    assert (home / ".cache/releases/test/fixture.whl").read_bytes() == content
    assert (home / "credentials.json").read_text() == "private fixture"
    assert not (home / "active.json").exists()
    assert not (home / "runtimes").exists()
    from nerv.platform import module_jobs, runtime_management
    monkeypatch.setenv("NERV_HOME", str(home))
    monkeypatch.setattr(runtime_management, "installations", lambda: [])
    assert module_jobs.catalog()["modules"][0]["id"] == "fixture-sim"
    (bundle / "fixture.whl").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        helper.prepare(bundle, home)
    assert (home / ".cache/releases/test/fixture.whl").read_bytes() == content


def test_missing_catalogue_is_reported_not_empty(monkeypatch):
    from fastapi import HTTPException
    from nerv.platform import module_jobs, server, runtime_management
    monkeypatch.setattr(runtime_management, "installations", lambda: [])
    monkeypatch.setattr(server.nerv, "_bound_runtime", object())
    def missing():
        raise ValueError("No release bundle")
    monkeypatch.setattr(module_jobs.installation, "read_manifest", missing)
    with pytest.raises(ValueError, match="release bundle"):
        module_jobs.catalog()
    with pytest.raises(HTTPException) as error:
        server.modules_catalog()
    assert error.value.status_code == 503
    assert "catalogue unavailable" in error.value.detail
