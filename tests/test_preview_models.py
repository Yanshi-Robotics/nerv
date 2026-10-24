"""Offline preview seeding tests; fixture credentials never reach a provider."""
import importlib.util
import json
from pathlib import Path

import pytest

from nerv.vlm.store import CredentialStore, ModelRegistry, private_write

spec = importlib.util.spec_from_file_location(
    "seed_preview_models", Path(__file__).parents[1] / "scripts/seed_preview_models.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


@pytest.mark.skipif(helper.os.name == "nt", reason="POSIX preview helper")
def test_seed_isolates_credentials_and_excludes_history(tmp_path):
    source, target = tmp_path / "main", tmp_path / "preview"
    CredentialStore(source).set("DEEPSEEK_API_KEY", "offline-fixture")
    private_write(source / "vlm.json", {"selection": "deepseek-flash", "checks": {"old": {}},
                                       "physical": {"old": {}}})
    (source / "memory").mkdir()
    (source / "memory/history.json").write_text("private history")
    before = {p.name: p.read_bytes() for p in source.glob("*.json")}
    helper.seed(source, target)
    assert ModelRegistry(target).selection() == "deepseek-flash"
    assert CredentialStore(target).resolve("DEEPSEEK_API_KEY") == "offline-fixture"
    assert set(p.name for p in target.iterdir()) == {"credentials.json", "vlm.json"}
    assert set(json.loads((target / "vlm.json").read_text())) == {"models", "efforts", "selection"}
    assert (target / "credentials.json").stat().st_mode & 0o777 == 0o600
    CredentialStore(target).set("DEEPSEEK_API_KEY", "preview-only-fixture")
    assert before == {p.name: p.read_bytes() for p in source.glob("*.json")}
    with pytest.raises(ValueError):
        helper.seed(source, target)
    assert CredentialStore(target).resolve("DEEPSEEK_API_KEY") == "preview-only-fixture"


@pytest.mark.parametrize("kind", ["same", "child", "parent", "symlink", "invalid"])
def test_reject_unsafe_or_invalid_input_before_writing(tmp_path, kind):
    source, target = tmp_path / "main", tmp_path / "preview"
    source.mkdir()
    if kind == "same":
        target = source
    elif kind == "child":
        target = source / "preview"
    elif kind == "parent":
        target = tmp_path
    elif kind == "symlink":
        target.mkdir()
        (target / "credentials.json").symlink_to(source / "missing.json")
    else:
        (source / "vlm.json").write_text("invalid-json")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        helper.seed(source, target)
    assert not (target / "vlm.json").exists() or target == source
