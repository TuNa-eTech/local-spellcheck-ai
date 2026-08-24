from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import zipfile
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("benchmark metric must be numeric")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError("benchmark metric must be finite")
    return converted


def canonical_unsigned(manifest: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in manifest.items() if key != "signature"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_private_key(path: Path) -> Ed25519PrivateKey:
    raw = path.read_bytes()
    if raw.startswith(b"-----BEGIN"):
        key = serialization.load_pem_private_key(raw, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("PEM private key must use Ed25519")
        return key
    if len(raw) != 32:
        try:
            raw = base64.b64decode(raw.strip(), validate=True)
        except ValueError as error:
            raise ValueError("private key must be 32 raw bytes or base64") from error
    if len(raw) != 32:
        raise ValueError("private key must decode to exactly 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(raw)


def quality_gate_from_reports(
    reports: list[Path], model_id: str, version: str, model_sha256: str
) -> dict[str, Any]:
    profiles: list[dict[str, Any]] = []
    corpus_hashes: set[str] = set()
    for path in reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("model") != {"id": model_id, "version": version}:
            raise ValueError("benchmark report targets a different model")
        if report.get("model_sha256") != model_sha256:
            raise ValueError("benchmark report targets different model bytes")
        corpus_hash = report.get("corpus_sha256")
        machine = report.get("machine")
        machine_memory = machine.get("memory_mb") if isinstance(machine, dict) else None
        documents = report.get("documents")
        precision = report.get("precision")
        recall = report.get("recall")
        latency = report.get("latency_seconds")
        p95 = latency.get("p95") if isinstance(latency, dict) else None
        peak_rss = report.get("peak_rss_mb")
        try:
            memory_value = finite_number(machine_memory)
            precision_value = finite_number(precision)
            recall_value = finite_number(recall)
            p95_value = finite_number(p95)
            peak_rss_value = finite_number(peak_rss)
        except ValueError as error:
            raise ValueError("benchmark report does not meet the M2 quality gate") from error
        if (
            not isinstance(corpus_hash, str)
            or len(corpus_hash) != 64
            or any(character not in "0123456789abcdef" for character in corpus_hash)
            or isinstance(documents, bool)
            or not isinstance(documents, int)
            or documents < 20
            or precision_value < 0.90
            or recall_value < 0.85
            or not 0 < p95_value <= 180
            or peak_rss_value <= 0
        ):
            raise ValueError("benchmark report does not meet the M2 quality gate")
        corpus_hashes.add(corpus_hash)
        profiles.append(
            {
                "machine_memory_mb": memory_value,
                "documents": documents,
                "precision": precision_value,
                "recall": recall_value,
                "p95_seconds": p95_value,
                "peak_rss_mb": peak_rss_value,
                "report_sha256": file_sha256(path),
            }
        )
    has_8gb = any(7000 <= item["machine_memory_mb"] <= 9216 for item in profiles)
    has_16gb = any(15000 <= item["machine_memory_mb"] <= 18432 for item in profiles)
    if len(profiles) < 2 or len(corpus_hashes) != 1 or not has_8gb or not has_16gb:
        raise ValueError("quality gate requires matching-corpus reports from 8 GB and 16 GB profiles")
    return {"corpus_sha256": corpus_hashes.pop(), "profiles": profiles}


def package_model(
    model: Path,
    license_file: Path,
    output: Path,
    private_key: Ed25519PrivateKey,
    benchmark_reports: list[Path],
    *,
    model_id: str,
    version: str,
    memory_mb: int,
    context_size: int = 2048,
    batch_size: int = 8,
    max_tokens: int = 512,
    timeout_seconds: int = 120,
    seed: int = 42,
    minimum_confidence: float = 0.8,
) -> dict[str, Any]:
    if not model.is_file() or not license_file.is_file():
        raise ValueError("model and license must be existing files")
    if (
        model.name == license_file.name
        or "manifest.json" in {model.name, license_file.name}
        or not 1 <= license_file.stat().st_size <= 1024 * 1024
    ):
        raise ValueError("model, license, and manifest must be distinct; license must be non-empty")
    if len(model_id) < 2 or not model_id[0].isalnum() or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in model_id
    ):
        raise ValueError("model_id must match ^[a-z0-9][a-z0-9._-]+$")
    if (
        not version.strip()
        or memory_mb < 1
        or not 512 <= context_size <= 32768
        or not 1 <= batch_size <= 64
        or not 32 <= max_tokens <= 4096
        or not 1 <= timeout_seconds <= 180
        or not math.isfinite(minimum_confidence)
        or not 0 <= minimum_confidence <= 1
    ):
        raise ValueError("invalid model runtime limits")
    model_hash = file_sha256(model)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "model_id": model_id,
        "version": version,
        "engine_protocol": 1,
        "file": model.name,
        "size": model.stat().st_size,
        "sha256": model_hash,
        "license_file": license_file.name,
        "memory_mb": memory_mb,
        "context_size": context_size,
        "batch_size": batch_size,
        "max_tokens": max_tokens,
        "timeout_seconds": timeout_seconds,
        "seed": seed,
        "minimum_confidence": minimum_confidence,
        "quality_gate": quality_gate_from_reports(
            benchmark_reports, model_id, version, model_hash
        ),
    }
    signature = private_key.sign(canonical_unsigned(manifest))
    manifest["signature"] = base64.b64encode(signature).decode("ascii")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", allowZip64=True) as package:
        package.write(model, model.name, compress_type=zipfile.ZIP_STORED)
        package.write(license_file, license_file.name, compress_type=zipfile.ZIP_DEFLATED)
        package.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
            compress_type=zipfile.ZIP_DEFLATED,
        )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a signed SoatVan .svmodel package")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--license", dest="license_file", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--benchmark-report", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--memory-mb", type=int, required=True)
    parser.add_argument("--context-size", type=int, default=2048)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--minimum-confidence", type=float, default=0.8)
    args = parser.parse_args()
    private_key = load_private_key(args.private_key)
    manifest = package_model(
        args.model,
        args.license_file,
        args.output,
        private_key,
        args.benchmark_report,
        model_id=args.model_id,
        version=args.version,
        memory_mb=args.memory_mb,
        context_size=args.context_size,
        batch_size=args.batch_size,
        max_tokens=args.max_tokens,
        timeout_seconds=args.timeout_seconds,
        seed=args.seed,
        minimum_confidence=args.minimum_confidence,
    )
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    print(f"Created {args.output} for {manifest['model_id']}@{manifest['version']}")
    print(f"SOATVAN_MODEL_PUBLIC_KEY={base64.b64encode(public_key).decode('ascii')}")


if __name__ == "__main__":
    main()
