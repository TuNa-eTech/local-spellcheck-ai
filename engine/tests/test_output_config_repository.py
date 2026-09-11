from pathlib import Path

import pytest

from soatvan.custom_rules.output_config_repository import (
    OutputConfig,
    SqliteOutputConfigRepository,
)


def test_output_config_dataclass_validation() -> None:
    cfg = OutputConfig(mode="new_file", backup_original=True)
    assert cfg.mode == "new_file"
    assert cfg.backup_original is True

    cfg_in_place = OutputConfig(mode="in_place", backup_original=False)
    assert cfg_in_place.mode == "in_place"
    assert cfg_in_place.backup_original is False

    with pytest.raises(ValueError, match="Invalid output mode"):
        OutputConfig(mode="invalid_mode")


def test_sqlite_output_config_repository_crud(tmp_path: Path) -> None:
    db_path = tmp_path / "preferences.db"
    repo = SqliteOutputConfigRepository(db_path)

    # Initial default
    cfg = repo.get_config()
    assert cfg.mode == "new_file"
    assert cfg.backup_original is True

    # Update mode to in_place
    updated = repo.set_config(mode="in_place")
    assert updated.mode == "in_place"
    assert updated.backup_original is True

    # Verify persistence
    persisted = repo.get_config()
    assert persisted.mode == "in_place"
    assert persisted.backup_original is True

    # Update backup_original to False
    updated2 = repo.set_config(backup_original=False)
    assert updated2.mode == "in_place"
    assert updated2.backup_original is False

    # New repo instance on same db
    repo2 = SqliteOutputConfigRepository(db_path)
    cfg2 = repo2.get_config()
    assert cfg2.mode == "in_place"
    assert cfg2.backup_original is False

    # Switch back to new_file
    updated3 = repo2.set_config(mode="new_file", backup_original=True)
    assert updated3.mode == "new_file"
    assert updated3.backup_original is True

    # Reject invalid mode
    with pytest.raises(ValueError, match="Invalid output mode"):
        repo2.set_config(mode="random_mode")
