from pathlib import Path
from unittest.mock import MagicMock

from soatvan.entrypoints.sidecar import EngineSidecar


def test_sidecar_seq2seq_config_get_and_update(tmp_path: Path) -> None:
    sidecar = EngineSidecar(local_data=tmp_path)

    # 1. Initial config
    res = sidecar.seq2seq_config_get({})
    assert res["model_dir"] == ""
    assert res["is_configured"] is False
    assert res["is_valid"] is False
    assert res["is_enabled"] is True

    # 2. Update model_dir
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")

    res = sidecar.seq2seq_config_update({"model_dir": str(model_dir)})
    assert res["model_dir"] == str(model_dir)
    assert res["is_configured"] is True
    assert res["is_valid"] is True
    assert res["is_enabled"] is True

    # 3. Update is_enabled only
    res = sidecar.seq2seq_config_update({"is_enabled": False})
    assert res["model_dir"] == str(model_dir)
    assert res["is_enabled"] is False

    # 4. Verify get returns updated state
    res = sidecar.seq2seq_config_get({})
    assert res["is_enabled"] is False


def test_sidecar_run_job_disables_seq2seq_when_is_enabled_false(tmp_path: Path) -> None:
    sidecar = EngineSidecar(local_data=tmp_path)
    # Mock seq2seq provider as ready
    mock_seq2seq = MagicMock()
    mock_seq2seq.is_ready.return_value = True
    sidecar.seq2seq = mock_seq2seq

    # Set is_enabled = False
    sidecar.seq2seq_config_repo.set_config(model_dir=str(tmp_path), is_enabled=False)

    # Mock processor.process to check the request passed
    mock_process = MagicMock()
    mock_result = MagicMock()
    mock_result.finding_count = 0
    mock_result.output_path = None
    mock_result.counts = {}
    mock_result.findings = ()
    mock_result.review = None
    mock_process.return_value = mock_result
    sidecar.processor.execute = mock_process
    sidecar.processor.process = mock_process

    # Call _run_job
    params = {
        "source_path": str(tmp_path / "input.docx"),
        "temporary_output_path": str(tmp_path / "out.docx"),
        "use_model": False,
        "full_review": False,
        "include_rule_findings": False,
    }
    # Create fake input file
    (tmp_path / "input.docx").write_bytes(b"dummy")

    token = MagicMock()
    token.is_cancelled = False
    sidecar._run_job("test-job-1", params, token)

    # Verify process request was called with use_seq2seq=False
    assert mock_process.called
    req = mock_process.call_args[0][0]
    assert req.use_seq2seq is False


def test_sidecar_run_job_enables_seq2seq_when_is_enabled_true(tmp_path: Path) -> None:
    sidecar = EngineSidecar(local_data=tmp_path)
    # Mock seq2seq provider as ready
    mock_seq2seq = MagicMock()
    mock_seq2seq.is_ready.return_value = True
    sidecar.seq2seq = mock_seq2seq

    # Set is_enabled = True
    sidecar.seq2seq_config_repo.set_config(model_dir=str(tmp_path), is_enabled=True)

    # Mock processor.process to check the request passed
    mock_process = MagicMock()
    mock_result = MagicMock()
    mock_result.finding_count = 0
    mock_result.output_path = None
    mock_result.counts = {}
    mock_result.findings = ()
    mock_result.review = None
    mock_process.return_value = mock_result
    sidecar.processor.execute = mock_process
    sidecar.processor.process = mock_process

    # Call _run_job
    params = {
        "source_path": str(tmp_path / "input.docx"),
        "temporary_output_path": str(tmp_path / "out.docx"),
        "use_model": False,
        "full_review": False,
        "include_rule_findings": False,
    }
    # Create fake input file
    (tmp_path / "input.docx").write_bytes(b"dummy")

    token = MagicMock()
    token.is_cancelled = False
    sidecar._run_job("test-job-2", params, token)

    # Verify process request was called with use_seq2seq=True
    assert mock_process.called
    req = mock_process.call_args[0][0]
    assert req.use_seq2seq is True
