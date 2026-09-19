# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

SoátVăn ("SoatVan-itowf") is an offline Vietnamese DOCX proofreading desktop app. Workflow: pick file → configure review → process → open output. The source DOCX is **immutable**; the output is a copy with yellow highlights + Word comments. There is no preview, no per-finding approval, no auto-rewrite of text.

Product docs (`README.md`, `docs/prd.md`, `docs/technical-design.md`, `docs/implementation-status.md`) are written in Vietnamese and are the authority on product decisions. UI strings are Vietnamese.

## Stack and layout

Three languages in one process tree:

- `apps/desktop/src` — Vanilla TypeScript + Vite UI (no framework). `main.ts` holds the whole view/state machine, `api.ts` is the only Tauri boundary, `contracts.ts` mirrors the JSON contracts.
- `apps/desktop/src-tauri/src` — Rust Tauri 2 host. `lib.rs` = commands + DOCX/output safety, `sidecar.rs` = `EngineBroker` (spawns and supervises the Python sidecar), `model.rs` = model package import/verify/activate.
- `engine/src/soatvan` — Python 3.12 engine, run as a **persistent sidecar** speaking NDJSON v1 over stdin/stdout.
- `contracts/*.schema.json` — JSON Schema for IPC, findings, model manifests, benchmarks. Treat these as the source of truth; `engine/tests/test_contracts.py` validates against them.

Data flow: UI → `invoke(tauri_command)` → Rust `EngineBroker.call()` → NDJSON frame → Python `Sidecar.dispatch()`. Job progress flows back as `job.progress` / `job.completed` / `job.no_findings` / `job.failed` events, re-emitted to the webview as Tauri events.

## Architecture rule (enforced by a test)

`soatvan/checking` and `soatvan/workflow` are the domain/use-case core and must **not** import `lxml`, `sqlite3`, `zipfile`, or `tauri`. `engine/tests/test_architecture.py` fails the build if they do. Adapters live outside:

- `workflow/ports.py` — all Protocols and DTOs (`DocumentPackage`, `ClassifierProvider`, `FullTextReviewer`, `Seq2SeqProvider`, `Finding` flows). Add new capabilities here first.
- `document/ooxml.py` (lxml/zip), `custom_rules/*` and `dictionary/*` (SQLite), `entrypoints/sidecar.py` (NDJSON) are the adapters.

`workflow/process.py::ProcessDocument.execute` is the single orchestration point for a job.

## Review modes

Derived from two booleans on `job.start`, validated both in the IPC schema and in `execute`:

| `use_model` | `full_review` | Mode |
|---|---|---|
| false | — | Rule engine only (fallback "kiểm tra cơ bản") |
| true | false | AI filter — LLM keeps/drops rule-produced candidates |
| true | true | AI full review — LLM discovers findings per token-budgeted chunk (default when the model supports it) |

`include_rule_findings=true` requires full review; it adds rule findings as candidates while the LLM still scans the whole text.

Three inference backends coexist: local GGUF via `llama-cpp-python` (`models/classifier.py`, `models/review.py`), cloud API (`models/cloud_api.py` + `llm_transport.py`, OpenAI/Gemini, user-configured), and an optional seq2seq speller (`models/seq2seq_speller.py`) that runs in a **separate subprocess** (`entrypoints/seq2seq_worker.py`) and releases the LLM runtime first so the two never hold memory simultaneously.

## Invariants to preserve

These are fail-closed by design — do not "fix" them into leniency:

- A full-review finding is accepted only on exact `paragraph_id + source_text + occurrence_index` match. Anything outside the target chunk or pointing at context-only text is dropped.
- Findings that cannot be anchored safely are not written. If none can be, the job fails with `DOCUMENT_FINDINGS_NOT_EXPORTABLE`.
- Partial coverage must surface as `status: "partial"` with timeout/invalid-output/retry counters — never silently reported as "no errors".
- No findings → the temp output is deleted and no copy is produced.
- Output writes are atomic and no-clobber; source file is never modified.
- No network model download path exists. `.svmodel` packages are verified (manifest, size, SHA-256, Ed25519 via compile-time `SOATVAN_MODEL_PUBLIC_KEY`); bare `.gguf` imports are marked `local_unverified`. `installed` ≠ `ready` — only a successful smoke-load promotes to `ready`.
- Custom prompts add terminology/criteria only; they must never alter the output schema. Stored limit 4,000 chars, transport 4,200.

## Commands

Dev (requires Node 24+, Rust 1.98.0, `uv`, Python 3.12):

```sh
uv sync --project engine --extra dev --extra model
npm install --prefix apps/desktop
npm run tauri -- dev       # use the Tauri window, not the Vite URL
```

`npm run dev` (browser-only) runs the UI against stubbed demo data in `api.ts` — no engine, no Tauri. Never use it to validate engine behavior.

Full gate (mirrors `.github/workflows/ci.yml`):

```sh
uv run --project engine pytest --cov=soatvan --cov-fail-under=85
uv run --project engine ruff check engine
uv run --project engine mypy --config-file engine/pyproject.toml
npm test --prefix apps/desktop
npm run build --prefix apps/desktop
cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml
cargo fmt --check --manifest-path apps/desktop/src-tauri/Cargo.toml
cargo clippy --all-targets --manifest-path apps/desktop/src-tauri/Cargo.toml -- -D warnings
```

Single test:

```sh
uv run --project engine pytest engine/tests/test_full_review.py::test_name
npm test --prefix apps/desktop -- -t "substring of test name"
cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml test_name
```

mypy is `strict` and ruff selects `E,F,I,UP,B,SIM` at line-length 100. Coverage floor is 85%.

Packaging (`scripts/build-windows.ps1` / `build-macos.sh` handle sync → PyInstaller `onedir` → copy into Tauri resources → bundle):

```sh
npm run build:windows    # NSIS .exe, Windows only
npm run build:macos      # .dmg, macOS only
```

The sidecar is platform- and arch-specific; it cannot be cross-built.

## Versioning

Never hand-edit versions. `scripts/bump-version.py` syncs `VERSION`, both `package.json`/`package-lock.json`, `tauri.conf.json`, `Cargo.toml`/`Cargo.lock`, `engine/pyproject.toml`, `soatvan/__init__.py`, and `engine/uv.lock`:

```sh
python3 scripts/bump-version.py --patch          # or --minor / --major / 0.2.1
```

Pushing a `vMAJOR.MINOR.PATCH` tag triggers the release workflow. Commits follow Conventional Commits (`feat(desktop):`, `fix:`, `refactor(classifier):`, `chore(release):`).

## Debugging

Rust host, Python `stderr`, and webview console errors all land in one rotating log:

- Windows: `%LOCALAPPDATA%\vn.soatvan.desktop\logs\soatvan.log`
- macOS: `~/Library/Logs/vn.soatvan.desktop/soatvan.log`

Sidecar `stdout` is reserved for NDJSON — never print to it. Use `stderr` (`target=engine`). Set `SOATVAN_DEV_LOG=1` for DEBUG level.

## Platform-gated checks

Windows-only gates run in CI, not locally: frozen sidecar without Python on `PATH`, Unicode/long paths, Open XML SDK validation (`tools/OpenXmlValidator`), NSIS standard-user install, Defender, zero-egress on startup, and Windows Job Object orphan prevention. See `docs/m0-m1-acceptance.md`. Don't claim these pass from a macOS run.

## Code navigation

This repo has a `.codegraph/` index. Prefer `codegraph explore "<symbols or question>"` (or the `codegraph_explore` MCP tool) over grep for locating and understanding code — `lib.rs` (~72 KB), `main.ts` (~128 KB), and `review.py` are large enough that grep-and-read is expensive.
