"""Display assets never escape the registered root or silently serve stale source."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from nerv.platform.explore import asset_path, read_manifest


@pytest.fixture
def display(tmp_path):
    source = tmp_path / "layout.py"
    source.write_text("source")
    directory = tmp_path / ".cache/explore/example"
    directory.mkdir(parents=True)
    (directory / "scene.glb").write_bytes(b"glTF")
    manifest = {"scene": "example", "schema_version": 1,
        "sources": {"layout.py": hashlib.sha256(source.read_bytes()).hexdigest()},
        "assets": [{"path": "scene.glb", "bytes": 4, "hash": hashlib.sha256(b"glTF").hexdigest()}]}
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return SimpleNamespace(name="example", assets_root=str(tmp_path), explore=".cache/explore/example/manifest.json")


def test_offline_display_does_not_need_simulation(display):
    _, manifest = read_manifest(display)
    assert manifest["scene"] == "example"
    assert asset_path(display, "scene.glb").read_bytes() == b"glTF"


def test_source_change_is_explicitly_stale(display):
    from pathlib import Path
    (Path(display.assets_root) / "layout.py").write_text("new source")
    with pytest.raises(ValueError, match="out of date"):
        read_manifest(display)


def test_same_size_asset_change_invalidates_cached_hash(display):
    asset = asset_path(display, "scene.glb")
    asset.write_bytes(b"BAD!")
    with pytest.raises(ValueError, match="changed"):
        asset_path(display, "scene.glb")


@pytest.mark.parametrize("name", ["../../../layout.py", "/etc/passwd", "missing.glb"])
def test_only_manifest_assets_are_servable(display, name):
    with pytest.raises((ValueError, FileNotFoundError)):
        asset_path(display, name)
