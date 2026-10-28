from pathlib import Path

from services.cpu_model import cpu_model


def test_amd_cpu_does_not_advertise_orphan_intel_ssbd(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda self: "vendor_id : AuthenticAMD\nflags : ssbd svm\n")
    assert cpu_model("kvm") == "host,ssbd=off"
    assert cpu_model("tcg") == "max"


def test_intel_and_supported_spec_ctrl_are_preserved(monkeypatch):
    for vendor in ("GenuineIntel", "AuthenticAMD"):
        monkeypatch.setattr(Path, "read_text", lambda self: f"vendor_id : {vendor}\nflags : ssbd spec_ctrl\n")
        assert cpu_model("kvm") == "host"


def test_unknown_host_is_not_reconfigured(monkeypatch):
    def unreadable(self):
        raise OSError("unavailable")
    monkeypatch.setattr(Path, "read_text", unreadable)
    assert cpu_model("kvm") == "host"
