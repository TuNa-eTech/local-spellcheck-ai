from pathlib import Path

import pytest

from soatvan.entrypoints.sidecar import EngineSidecar


def test_sidecar_output_config_get_and_update(tmp_path: Path) -> None:
    sidecar = EngineSidecar(local_data=tmp_path)

    # 1. Initial config
    res = sidecar.dispatch("output_config.get", {})
    assert res["mode"] == "new_file"
    assert res["backup_original"] is True

    # 2. Update to in_place
    res = sidecar.dispatch("output_config.update", {"mode": "in_place"})
    assert res["mode"] == "in_place"
    assert res["backup_original"] is True

    # 3. Update backup_original
    res = sidecar.dispatch("output_config.update", {"backup_original": False})
    assert res["mode"] == "in_place"
    assert res["backup_original"] is False

    # 4. Verify get returns updated state
    res = sidecar.dispatch("output_config.get", {})
    assert res["mode"] == "in_place"
    assert res["backup_original"] is False

    # 5. Invalid mode error
    with pytest.raises(ValueError, match="Invalid output mode"):
        sidecar.dispatch("output_config.update", {"mode": "invalid_mode"})
