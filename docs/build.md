# Build SoátVăn Desktop

## Nguyên tắc

SoátVăn gồm Tauri host và Python sidecar được đóng gói bằng PyInstaller `onedir`. Cả hai chứa mã native, vì vậy phải build trên đúng hệ điều hành và kiến trúc đích:

- Windows x64 build installer Windows x64.
- macOS Apple Silicon build DMG arm64.
- macOS Intel build DMG x86_64.

Không copy sidecar đã build giữa Windows, macOS, arm64 và x86_64.

## Yêu cầu chung

- Git.
- Node.js 24 trở lên và npm.
- Rust 1.98.0 qua `rustup`; repository đã pin phiên bản bằng `rust-toolchain.toml`.
- `uv` và Python 3.12 do `uv` quản lý.
- Dependency hệ thống của Tauri 2 cho nền tảng đang build.

Chạy script từ bất kỳ thư mục nào trong shell đều được; script tự xác định repository root. Dependency Python được cài từ `engine/uv.lock`, dependency frontend được cài bằng `npm ci` từ lockfile.

Nếu chưa có toolchain đã pin:

```sh
rustup toolchain install 1.98.0 --profile minimal --component clippy,rustfmt
```

## Build Windows `.exe`

Chạy trên Windows bằng PowerShell:

```powershell
npm run build:windows
```

Hoặc gọi trực tiếp:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-windows.ps1
```

Artifact NSIS nằm tại:

```text
apps/desktop/src-tauri/target/release/bundle/nsis/*.exe
```

Installer dùng WebView2 `offlineInstaller`, vì vậy kích thước lớn hơn nhưng không cần tải WebView2 trong lúc cài đặt.

### Windows acceptance

Không chạy hoặc mô phỏng Windows acceptance trên macOS. Workflow `.github/workflows/ci.yml` chạy toàn bộ phần phụ thuộc Windows trên `windows-latest`:

- chạy sidecar PyInstaller trực tiếp khi `PATH` không chứa Python;
- xử lý DOCX trong đường dẫn Unicode, có khoảng trắng và dài;
- kiểm tra cancel không để output tạm;
- validate output bằng Microsoft Open XML SDK;
- build/cài NSIS với manifest `asInvoker`;
- launch app đã cài, xác nhận sidecar được bundle, không có established TCP connection và không còn process sau khi host bị kill;
- scan installer bằng Microsoft Defender.

Script kiểm tra package có thể gọi lại trên Windows runner:

```powershell
./scripts/test-windows-package.ps1 -InstallerPath "apps/desktop/src-tauri/target/release/bundle/nsis/SoatVan-setup.exe"
```

## Build macOS `.dmg`

Chạy trên macOS:

```sh
npm run build:macos
```

Hoặc gọi trực tiếp:

```sh
./scripts/build-macos.sh
```

Artifact nằm tại:

```text
apps/desktop/src-tauri/target/release/bundle/dmg/*.dmg
```

Script build đúng kiến trúc của máy đang chạy. Để phát hành cho cả Apple Silicon và Intel, nên chạy hai job macOS riêng và phát hành hai DMG có tên kiến trúc rõ ràng. Universal binary cần hợp nhất cả Tauri executable lẫn toàn bộ native library trong PyInstaller sidecar, nên không được tạo chỉ bằng cách đổi target của Tauri.

## Model và cấu hình build

Model không được đóng trong installer. Tải model chỉ được compile khi có đủ các biến môi trường sau:

- `SOATVAN_MODEL_ENDPOINT`: URL HTTPS chính xác.
- `SOATVAN_MODEL_ALLOWLIST`: hostname được phép, phân cách bằng dấu phẩy.
- `SOATVAN_MODEL_PUBLIC_KEY`: Ed25519 public key dạng base64.

Nếu không có đủ cấu hình, ứng dụng vẫn build và chạy rule-only; chức năng tải model fail-closed. Import gói model offline vẫn yêu cầu manifest, checksum và chữ ký hợp lệ.

### Benchmark và đóng gói model M2

Runtime model được cài cùng extra `model`:

```sh
uv sync --project engine --extra dev --extra model --locked
```

Quy trình phê duyệt hiện tại là: tạo manifest nháp chứa identity/runtime limits → chạy cùng một corpus candidate-filter đã duyệt trên máy 8 GB và 16 GB → ký gói bằng hai report. Lệnh benchmark ghi SHA-256 của cả GGUF và corpus vào report:

```sh
uv run --project engine python tools/benchmark_model.py \
  --model approved-model.gguf \
  --manifest benchmark-manifest.json \
  --corpus approved-corpus.json \
  --output benchmark-8gb.json \
  --max-p95-seconds 120 \
  --max-rss-mb 7000
```

Chạy lại trên profile 16 GB để tạo `benchmark-16gb.json`. Hai report phải cùng model SHA-256, model ID/version và corpus SHA-256; precision phải ≥90%, recall ≥85%. Tạo Ed25519 key ngoài repository rồi đóng gói:

```sh
openssl genpkey -algorithm Ed25519 -out model-signing-key.pem
uv run --project engine python tools/package_model.py \
  --model approved-model.gguf \
  --license LICENSE.txt \
  --private-key model-signing-key.pem \
  --benchmark-report benchmark-8gb.json \
  --benchmark-report benchmark-16gb.json \
  --output approved-model.svmodel \
  --model-id approved-model \
  --version 1.0.0 \
  --memory-mb 3000
```

Tool in public key base64 cần đưa vào `SOATVAN_MODEL_PUBLIC_KEY`. Không commit private key, GGUF, corpus khách hàng hoặc report chứa metadata máy. Cấu trúc corpus và manifest nháp xem tại [`model-benchmark.md`](model-benchmark.md).

Hai report do lệnh trên tạo chỉ là evidence cho **AI filter**, và `tools/package_model.py` mặc định ký package với `capabilities.full_review=false`. Trước khi phát hành **AI full review**, phải chạy benchmark riêng bằng production token-aware chunker trên toàn bộ supported block, đo discovery precision/recall, coverage, partial semantics, latency và RAM ở cả hai profile. Chỉ manifest schema v2 `release_signed` có `capabilities.full_review=true` mới mở chức năng; trạng thái `ready` theo gate filter không tự động phê duyệt full review.

## Chữ ký cho nhu cầu cá nhân

Dự án mặc định dùng chữ ký nội bộ, không cần certificate thương mại và không lưu private key trong repository.

### Windows self-signed

`scripts/build-windows.ps1` nạp PFX code-signing cố định, xác minh private key, thời hạn và EKU Code Signing, rồi đăng ký tạm vào `CurrentUser\My` và `CurrentUser\TrustedPeople` bằng .NET `X509Store`:

- Subject: `CN=SoatVan Personal Use`.
- RSA 3072 bit.
- SHA-256.
- Hiệu lực 5 năm.
- Cùng một identity được dùng lại giữa các release.

GitHub Actions lấy PFX base64 và mật khẩu từ hai repository secret `SOATVAN_WINDOWS_CERT_PFX_BASE64` và `SOATVAN_WINDOWS_CERT_PASSWORD`. Build local có thể dùng `SOATVAN_WINDOWS_CERT_PFX` trỏ tới file PFX cùng `SOATVAN_WINDOWS_CERT_PASSWORD`. Script không dùng PowerShell `Cert:` drive: nó ký Python sidecar/portable bằng `signtool`, yêu cầu Tauri ký app/installer bằng thumbprint, xuất public certificate `SoatVan-Personal-CodeSigning.cer`, rồi gỡ key khỏi các store tạm và xóa PFX tạm.

Certificate self-signed chỉ phù hợp kiểm thử hoặc máy cá nhân. Nó không tạo uy tín SmartScreen. Trước khi trust file `.cer` trên một máy khác, phải đối chiếu SHA-256/checksum từ Release và hiểu rằng certificate được thêm vào trust store có quyền xác nhận code ký bởi certificate đó. PFX và mật khẩu chỉ nằm trong GitHub Secrets, không nằm trong repository hoặc release artifact.

### macOS ad-hoc

Tauri được cấu hình `bundle.macOS.signingIdentity: "-"`, tức pseudo-identity ad-hoc. Script build giữ lại `.app`, chạy `codesign --verify --deep --strict` và xác minh checksum DMG bằng `hdiutil verify`.

Ad-hoc signing không phải Apple Developer ID và không qua notarization. Khi mở app tải từ GitHub, macOS vẫn có thể yêu cầu cho phép thủ công trong Privacy & Security. Không thể tạo Apple Developer ID hợp lệ bằng thông số giả định; identity thật phải do Apple cấp và gắn với Apple ID/Team ID.

Nếu sau này phát hành công khai:

- Windows: dùng Authenticode certificate thật hoặc Azure Trusted Signing.
- macOS: dùng Developer ID Application, hardened runtime và notarization.
- Certificate/password phải nằm trong GitHub Secrets hoặc secret manager, không commit vào repository.

## GitHub Release tự động

Workflow `.github/workflows/release.yml` chạy khi push tag bắt đầu bằng `v`. Tag hợp lệ phải có dạng `vMAJOR.MINOR.PATCH`, trỏ tới commit thuộc nhánh `main` và khớp version trong ba manifest:

- `apps/desktop/package.json`.
- `apps/desktop/src-tauri/tauri.conf.json`.
- `engine/pyproject.toml`.

Ví dụ phát hành version `0.1.0`:

```sh
git tag -a v0.1.0 -m "SoátVăn v0.1.0"
git push origin v0.1.0
```

Pipeline chạy test trước, sau đó build song song:

- Windows x64 NSIS `.exe`.
- macOS arm64 `.dmg`.
- macOS Intel x64 `.dmg`.

Khi tất cả job thành công, pipeline tạo `SHA256SUMS.txt`, sinh release notes và publish GitHub Release gắn với tag đã tồn tại. Nếu một job thất bại, Release không được tạo; sửa source, tăng patch version và tạo tag mới. Không di chuyển hoặc tái sử dụng tag đã phát hành.

Artifact Windows được self-sign và đính kèm public `.cer`; artifact macOS được ad-hoc sign. Cả hai chỉ dành cho sử dụng cá nhân, không nên phân phối công khai.

## Dữ liệu tạm khi build

PyInstaller tạo `engine/build` và `engine/dist`. Script đồng bộ bản `onedir` vào `apps/desktop/src-tauri/resources/engine`; các nội dung sinh ra này đã được `.gitignore` loại trừ. File `README.txt` trong thư mục resource được giữ lại làm placeholder của source tree.
