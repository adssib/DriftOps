import subprocess
import sys

from driftops.config import Settings


def test_help_lists_every_component():
    out = subprocess.run(
        [sys.executable, "-m", "driftops", "--help"], capture_output=True, text=True, check=True
    ).stdout
    for cmd in ("serve", "simulate", "seed", "champion", "mlflow"):
        assert cmd in out


def test_settings_read_env(monkeypatch):
    monkeypatch.setenv("DRIFTOPS_MODEL_VERSION", "7")
    monkeypatch.setenv("DRIFTOPS_CORS_ORIGINS", "https://a.example, https://b.example")
    s = Settings.from_env()
    assert s.model_version == 7
    assert s.cors_origins == ["https://a.example", "https://b.example"]
    assert s.lookup_timeout_ms == 200


def test_settings_defaults_without_env(monkeypatch):
    for k in ("DRIFTOPS_MODEL_VERSION", "DRIFTOPS_CORS_ORIGINS"):
        monkeypatch.delenv(k, raising=False)
    assert Settings.from_env().model_version == 1
