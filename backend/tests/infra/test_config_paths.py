from pathlib import Path

from valuz_agent.infra.config import Settings


def test_log_dir_is_independent_from_templated_data_dir() -> None:
    settings = Settings(
        data_dir=Path("~/.valuz-dev/{user_id}"),
        log_dir=Path("~/.valuz-dev/logs"),
    )

    assert settings.log_dir == Path("~/.valuz-dev/logs")
    assert settings.log_file == Path("~/.valuz-dev/logs/backend.log")
    assert "{user_id}" not in str(settings.log_file)


def test_log_dir_defaults_under_data_dir(monkeypatch, tmp_path: Path) -> None:
    """Pointing VALUZ_DATA_DIR elsewhere moves the logs with it — a dev/test
    backend must not write into the packaged app's logs by omission."""
    monkeypatch.delenv("VALUZ_LOG_DIR", raising=False)  # sandbox pins it
    settings = Settings(data_dir=tmp_path / "root")

    assert settings.log_dir == tmp_path / "root" / "logs"


def test_log_dir_default_strips_user_template(monkeypatch) -> None:
    monkeypatch.delenv("VALUZ_LOG_DIR", raising=False)  # sandbox pins it
    settings = Settings(data_dir=Path("/data/valuz/{user_id}"))

    assert settings.log_dir == Path("/data/valuz/logs")
    assert "{user_id}" not in str(settings.log_file)


def test_user_skill_staging_dir_env_alias(monkeypatch, tmp_path) -> None:
    staging_dir = tmp_path / "{user_id}" / "skill-staging"

    monkeypatch.setenv("VALUZ_USER_SKILL_STAGING_DIR", str(staging_dir))

    assert Settings().user_skill_staging_dir == staging_dir


def test_user_temp_dir_env_alias(monkeypatch, tmp_path) -> None:
    temp_dir = tmp_path / "{user_id}" / "tmp"

    monkeypatch.setenv("VALUZ_USER_TEMP_DIR", str(temp_dir))

    assert Settings().user_temp_dir == temp_dir
