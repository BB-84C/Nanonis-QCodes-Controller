from pathlib import Path

from nspmctl import __version__
from nspmctl.config import load_settings


def test_package_exposes_version() -> None:
    assert isinstance(__version__, str)
    assert __version__


def test_default_settings_load() -> None:
    settings = load_settings(env={})
    assert settings.nanonis.host == "127.0.0.1"
    assert settings.nanonis.ports == (3364, 6501, 6502, 6503, 6504)
    assert settings.safety.allow_writes is True
    assert settings.safety.default_ramp_interval_s == 0.05


def test_env_overrides_settings() -> None:
    settings = load_settings(
        env={
            "NANONIS_HOST": "192.168.1.20",
            "NANONIS_PORTS": "9000-9001",
            "NANONIS_TIMEOUT_S": "1.2",
            "NANONIS_RETRY_COUNT": "3",
            "NANONIS_BACKEND": "custom_adapter",
            "NANONIS_ALLOW_WRITES": "true",
            "NANONIS_DRY_RUN": "false",
            "NANONIS_DEFAULT_RAMP_INTERVAL_S": "0.2",
        }
    )

    assert settings.nanonis.host == "192.168.1.20"
    assert settings.nanonis.ports == (9000, 9001)
    assert settings.nanonis.timeout_s == 1.2
    assert settings.nanonis.retry_count == 3
    assert settings.nanonis.backend == "custom_adapter"
    assert settings.safety.allow_writes is True
    assert settings.safety.dry_run is False
    assert settings.safety.default_ramp_interval_s == 0.2


def test_yaml_runtime_overrides(tmp_path: Path) -> None:
    config_file = tmp_path / "settings.yaml"
    config_file.write_text(
        "\n".join(
            [
                "nanonis:",
                "  host: 127.0.0.1",
                "  ports: [6501]",
                "safety:",
                "  allow_writes: true",
                "  dry_run: false",
                "  default_ramp_interval_s: 0.1",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(config_file=config_file, env={})

    assert settings.safety.allow_writes is True
    assert settings.safety.dry_run is False
    assert settings.safety.default_ramp_interval_s == 0.1
