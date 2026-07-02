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
