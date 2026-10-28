import pytest
from core.errors import BuilderError
from core.live_settings import validate_live_settings
from services.target_vm import TargetVM


@pytest.mark.parametrize("value", [[], {"ram_gib": True}, {"ram_gib": 0}, {"cpus": 65},
    {"gpu_memory_mib": 4097}, {"resolution": "7680x4320"}, {"gpu": "-device evil"}, {"args": []}])
def test_rejects_invalid_live_settings(value):
    with pytest.raises(BuilderError):
        validate_live_settings(value)


def test_software_mode_never_probes_host_gpu(monkeypatch):
    def unexpected():
        pytest.fail("Software mode must not open a host GPU")
    monkeypatch.setattr("services.graphics_devices.render_devices", unexpected)
    display, device, devices = TargetVM(None).graphics({"gpu": "software"})
    assert display == "none" and not devices and device.startswith("virtio-vga,")


def test_required_acceleration_reports_missing_gpu(monkeypatch):
    monkeypatch.setattr("services.graphics_devices.render_devices", lambda: (None, {}))
    with pytest.raises(BuilderError, match="Accelerated 3D is unavailable"):
        TargetVM(None).graphics({"gpu": "accelerated"})
