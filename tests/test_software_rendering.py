"""CPU device selection never initializes a hardware EGL display."""
import httpx
import pytest
from nerv.world.rendering import configure_software_egl, software_device_index


def test_software_device_is_discovered_not_assumed_first():
    assert software_device_index(["EGL_EXT_device_drm", "EGL_EXT_device_drm", "EGL_MESA_device_software"]) == 2
    assert software_device_index(["EGL_MESA_device_software", "EGL_EXT_device_drm"]) == 0


def test_explicit_hardware_device_is_rejected_before_initialization():
    with pytest.raises(RuntimeError, match="not software"):
        software_device_index(["EGL_EXT_device_drm", "EGL_MESA_device_software"], "0")
    assert software_device_index(["EGL_EXT_device_drm", "EGL_MESA_device_software"], "1") == 1
    with pytest.raises(RuntimeError, match="no software device"):
        software_device_index(["EGL_EXT_device_drm"])


def test_gpu_and_non_egl_configuration_unchanged(monkeypatch):
    monkeypatch.setenv("MUJOCO_EGL_DEVICE_ID", "configured-device")
    monkeypatch.setenv("MUJOCO_GL", "egl")
    monkeypatch.delenv("LIBGL_ALWAYS_SOFTWARE", raising=False)
    configure_software_egl()
    monkeypatch.setenv("MUJOCO_GL", "osmesa")
    monkeypatch.setenv("LIBGL_ALWAYS_SOFTWARE", "1")
    configure_software_egl()
    import os
    assert os.environ["MUJOCO_EGL_DEVICE_ID"] == "configured-device"


def test_offline_world_control_returns_service_unavailable(monkeypatch):
    from fastapi.testclient import TestClient
    from nerv.platform import server
    def unavailable(_):
        raise httpx.ConnectError("connection refused")
    monkeypatch.setattr(server.nerv.world_controls, "capabilities", unavailable)
    response = TestClient(server.app).get("/api/sessions/offline-fixture/world-control")
    assert response.status_code == 503
    assert response.json()["detail"] == "World node unavailable. End this instance and create a new one."
