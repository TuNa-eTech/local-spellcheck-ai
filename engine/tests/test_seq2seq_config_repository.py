import sqlite3
from pathlib import Path

from soatvan.custom_rules.seq2seq_config_repository import (
    Seq2SeqConfig,
    SqliteSeq2SeqConfigRepository,
)


def test_seq2seq_config_dataclass_properties(tmp_path: Path) -> None:
    # 1. Chưa cấu hình
    cfg = Seq2SeqConfig(model_dir="", is_enabled=True)
    assert not cfg.is_configured
    assert not cfg.is_valid()
    assert not cfg.is_active

    # 2. Cấu hình nhưng thư mục không tồn tại / thiếu config.json
    invalid_dir = tmp_path / "invalid_model"
    invalid_dir.mkdir()
    cfg2 = Seq2SeqConfig(model_dir=str(invalid_dir), is_enabled=True)
    assert cfg2.is_configured
    assert not cfg2.is_valid()
    assert not cfg2.is_active

    # 3. Thư mục hợp lệ và is_enabled=True
    valid_dir = tmp_path / "valid_model"
    valid_dir.mkdir()
    (valid_dir / "config.json").write_text("{}", encoding="utf-8")
    cfg3 = Seq2SeqConfig(model_dir=str(valid_dir), is_enabled=True)
    assert cfg3.is_configured
    assert cfg3.is_valid()
    assert cfg3.is_active

    # 4. Thư mục hợp lệ nhưng is_enabled=False -> is_active là False
    cfg4 = Seq2SeqConfig(model_dir=str(valid_dir), is_enabled=False)
    assert cfg4.is_configured
    assert cfg4.is_valid()
    assert not cfg4.is_active


def test_sqlite_seq2seq_config_repository_crud(tmp_path: Path) -> None:
    db_path = tmp_path / "preferences.db"
    repo = SqliteSeq2SeqConfigRepository(db_path)

    # Mặc định ban đầu
    cfg = repo.get_config()
    assert cfg.model_dir == ""
    assert cfg.is_enabled is True

    # Cập nhật đường dẫn
    cfg = repo.set_config(model_dir="/path/to/model")
    assert cfg.model_dir == "/path/to/model"
    assert cfg.is_enabled is True

    # Cập nhật chỉ bật/tắt (is_enabled=False)
    cfg = repo.set_config(is_enabled=False)
    assert cfg.model_dir == "/path/to/model"
    assert cfg.is_enabled is False

    # Cập nhật cả hai
    cfg = repo.set_config(model_dir="/new/path", is_enabled=True)
    assert cfg.model_dir == "/new/path"
    assert cfg.is_enabled is True


def test_sqlite_seq2seq_config_migration_from_legacy_db(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_preferences.db"
    # Giả lập DB cũ chỉ có cột model_dir
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE seq2seq_config (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                model_dir TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            "INSERT INTO seq2seq_config (id, model_dir) VALUES (1, '/legacy/model')"
        )

    # Khởi tạo repository, kiểm tra migration tự động thêm cột is_enabled
    repo = SqliteSeq2SeqConfigRepository(db_path)
    cfg = repo.get_config()
    assert cfg.model_dir == "/legacy/model"
    assert cfg.is_enabled is True

    # Kiểm tra cập nhật sau migration
    cfg = repo.set_config(is_enabled=False)
    assert cfg.model_dir == "/legacy/model"
    assert cfg.is_enabled is False
