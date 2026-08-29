from pathlib import Path

from soatvan.custom_rules.ai_config_repository import SqliteAiConfigRepository


def test_ai_config_crud(tmp_path: Path) -> None:
    repo = SqliteAiConfigRepository(tmp_path / "preferences.db")
    assert repo.get_active_config() is None
    assert repo.list_configs() == []

    created = repo.upsert_config(
        provider="openai",
        api_key="sk-test123456789",
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
        is_active=True,
    )
    assert created.provider == "openai"
    assert created.is_active is True
    assert repo.get_active_config() == created

    # Add second provider
    gemini = repo.upsert_config(
        provider="gemini",
        api_key="AIzaSyTestKey",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_name="gemini-2.5-flash",
        is_active=False,
    )
    assert gemini.provider == "gemini"
    configs = repo.list_configs()
    assert len(configs) == 2

    # Switch active
    repo.set_active_provider("gemini")
    active = repo.get_active_config()
    assert active is not None
    assert active.provider == "gemini"
    assert active.is_active is True

    # Switch to local (no active cloud config)
    repo.set_active_provider("local")
    assert repo.get_active_config() is None

    # Test masked_key
    assert created.masked_key() == "sk-t...6789"
    short_config = repo.upsert_config(
        provider="other",
        api_key="short",
        base_url="https://example.com",
        model_name="m1",
    )
    assert short_config.masked_key() == "******"
