"""Select a software EGL device before Mesa can initialize a hardware display."""
from __future__ import annotations

import os

SOFTWARE_DEVICE_EXTENSION = "EGL_MESA_device_software"


def software_device_index(extensions: list[str], requested: str | None = None) -> int:
    candidates = [index for index, value in enumerate(extensions)
                  if SOFTWARE_DEVICE_EXTENSION in value.split()]
    if requested is not None:
        index = int(requested)
        if index not in candidates:
            raise RuntimeError("CPU rendering requires a software EGL device; the configured device is not software")
        return index
    if not candidates:
        raise RuntimeError("CPU rendering requested, but EGL reports no software device")
    return candidates[0]


def configure_software_egl() -> None:
    # Apply only to an explicitly requested CPU EGL configuration. GPU rendering
    # and other backends keep their own device-selection rules.
    if os.environ.get("MUJOCO_GL", "egl").lower() != "egl":
        return
    if os.environ.get("LIBGL_ALWAYS_SOFTWARE", "").lower() not in {"1", "true", "yes"}:
        return
    import ctypes
    from mujoco.egl import egl_ext as egl

    address = egl.eglGetProcAddress("eglQueryDeviceStringEXT")
    if not address:
        raise RuntimeError("EGL device query is unavailable for CPU rendering")
    query = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_void_p, egl.EGLint)(address)
    devices = egl.eglQueryDevicesEXT()
    extensions = [(query(device, egl.EGL_EXTENSIONS) or b"").decode() for device in devices]
    index = software_device_index(extensions, os.environ.get("MUJOCO_EGL_DEVICE_ID"))
    os.environ["MUJOCO_EGL_DEVICE_ID"] = str(index)
