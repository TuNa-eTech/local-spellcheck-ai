# Thiết kế Kỹ thuật: Lớp phủ Khởi động Ứng dụng (Startup Loading Overlay)

- **Ngày tạo**: 2026-09-09
- **Trạng thái**: Chờ người dùng duyệt (Pending User Review)
- **Phạm vi**: Giao diện Desktop (`apps/desktop/src/main.ts`, `apps/desktop/src/styles.css`), Điều phối khởi tạo (`api`), Kiểm thử tự động (`apps/desktop/tests/workflow.test.ts`)

---

## 1. Mục tiêu và Bối cảnh

### 1.1 Vấn đề
Khi khởi động ứng dụng, quá trình tải dữ liệu cấu hình (`aiConfig`, `customRules`, `seq2seqConfig`) và đặc biệt là nạp mô hình AI cục bộ (`modelStatus` gọi vào `llama.cpp` và tính SHA-256) diễn ra ngầm ở nền. Trong khoảng thời gian vài giây này:
1. Giao diện chính (Workflow) đã hiển thị đầy đủ nhưng trạng thái mô hình và quy tắc chưa sẵn sàng.
2. Nếu người dùng nhấn ngay vào nút **Cài đặt**, màn hình Cài đặt sẽ rơi vào trạng thái bị khóa (`settingsLoading = true`), các tab và nút bấm bị vô hiệu hóa tạm thời, gây cảm giác ứng dụng bị đơ hoặc lỗi giao diện.
3. Người dùng chưa có phản hồi thị giác rõ ràng rằng ứng dụng đang trong quá trình chuẩn bị hệ thống ban đầu.

### 1.2 Mục tiêu cải tiến
- **Trải nghiệm khởi động mượt mà và trực quan**: Hiển thị lớp phủ mờ (**Loading Overlay**) chặn toàn bộ tương tác chuột và phím cho đến khi các cấu hình và mô hình AI khởi tạo xong.
- **Giữ nền ứng dụng mờ ảo phía sau**: Người dùng vẫn nhìn thấy bố cục quen thuộc của ứng dụng phía sau lớp kính mờ (`backdrop-filter: blur()`), tạo cảm giác ứng dụng đã nạp xong bộ khung và chỉ đang khởi động các dịch vụ.
- **Thông điệp tinh gọn**: Hiển thị spinner cùng thông báo ngắn gọn *"Đang chuẩn bị ứng dụng, vui lòng chờ…"*.
- **Xử lý lỗi tin cậy**: Không cho phép bỏ qua; nếu gặp lỗi nghiêm trọng (mất kết nối backend/engine), overlay chuyển sang hiển thị thông báo lỗi kèm nút **"Thử lại"**.

---

## 2. Thiết kế Trạng thái & Luồng Dữ liệu (State & Data Flow)

### 2.1 Mở rộng Trạng thái Ứng dụng (`state`)
Trong [`apps/desktop/src/main.ts`](file:///Users/anhtu/MySpace/Freelance/local-spellcheck-ai/apps/desktop/src/main.ts), bổ sung các trường sau vào `state`:

```typescript
interface State {
  // ... các trường hiện tại
  appInitializing: boolean;    // true khi app đang trong quá trình nạp khởi động
  appInitError: string | null;  // null hoặc thông báo lỗi nếu khởi động thất bại
}
```

Giá trị khởi tạo:
- `appInitializing: true`
- `appInitError: null`

### 2.2 Luồng Khởi động (`initializeApp`)
Tách logic khởi động độc lập thành hàm `initializeApp()` để có thể gọi lại khi bấm "Thử lại":

```
[Mở ứng dụng] -> appInitializing = true -> render() (Overlay hiển thị)
                       │
                       ▼
        Promise.allSettled([aiConfigGet, customRuleList, seq2seqConfigGet])
                       │
                       ▼
        api.modelStatus(initialModelPreference)
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
   [Thành công]                   [Thất bại]
        │                             │
appInitializing = false       appInitError = "Không thể kết nối..."
render() (Overlay biến mất)   render() (Hiển thị nút "Thử lại")
                                      │
                                      ▼ [Người dùng bấm "Thử lại"]
                               initializeApp()
```

Chi tiết các bước trong `initializeApp()`:
1. Đặt `state.appInitializing = true`, `state.appInitError = null`, gọi `render()`.
2. Gửi đồng thời các truy vấn:
   - `api.aiConfigGet()`
   - `api.customRuleList()`
   - `api.seq2seqConfigGet().catch(() => null)`
3. Cập nhật dữ liệu vào `state` tương ứng:
   - Cập nhật `state.aiConfig` và các draft Cloud AI.
   - Cập nhật `state.customRules` và đồng bộ lựa chọn mặc định.
   - Cập nhật `state.seq2seqConfig`.
4. Gọi tiếp `api.modelStatus(initialModelPreference)`.
   - Cập nhật `state.model`, tính toán `state.useModel`, `state.fullReview`.
5. Đặt `state.appInitializing = false`, `render()`.
6. **Bắt lỗi (Error Handling)**: Nếu toàn bộ chuỗi khởi tạo bị lỗi hoặc crash sidecar không thể kết nối:
   - Gán `state.appInitError = "Không thể khởi động dịch vụ ứng dụng. Hãy thử lại."`.
   - Giữ `state.appInitializing = true` và `render()`.

---

## 3. Thiết kế Giao diện (UI/UX) & Khả năng Tiếp cận (Accessibility)

### 3.1 Bố cục HTML của Lớp phủ Overlay
Trong hàm `render()`, thêm khối HTML overlay lên trên cùng khi `state.appInitializing === true`:

```html
<div class="app-init-overlay" role="dialog" aria-modal="true" aria-labelledby="app-init-title">
  <!-- Trạng thái Đang nạp -->
  <div class="app-init-card" role="status">
    <div class="spinner" aria-hidden="true"></div>
    <p class="app-init-title" id="app-init-title">Đang chuẩn bị ứng dụng, vui lòng chờ…</p>
  </div>
</div>
```

Khi có lỗi (`state.appInitError !== null`):
```html
<div class="app-init-overlay" role="dialog" aria-modal="true" aria-labelledby="app-init-title">
  <div class="app-init-card app-init-card--error" role="alert">
    <div class="app-init-error-icon" aria-hidden="true">⚠️</div>
    <h2 class="app-init-title" id="app-init-title">Không thể chuẩn bị ứng dụng</h2>
    <p class="app-init-message">${escape(state.appInitError)}</p>
    <button class="button button--primary" id="retry-app-init" type="button">Thử lại</button>
  </div>
</div>
```

### 3.2 Kiểu dáng CSS (`styles.css`)
- **Vị trí và nền mờ**:
  - `position: fixed; inset: 0; z-index: 1000;`
  - `display: grid; place-items: center;`
  - `background: rgba(15, 23, 42, 0.45);` (hoặc biến màu theme phù hợp cả Light/Dark)
  - `backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px);`
- **Khối thông tin (`.app-init-card`)**:
  - Bo góc mềm mại (`border-radius: var(--radius-card)`), đổ bóng nổi (`box-shadow`).
  - Nền thẻ hộp (`background: var(--color-surface)`), viền tinh tế (`border: 1px solid var(--color-rule)`).
  - Căn giữa nội dung, khoảng cách cân đối, spinner kích thước vừa vặn.
- **Khóa tương tác bên dưới**:
  - Khi `appInitializing === true`, các phần tử layout chính bên dưới gắn thuộc tính `inert` hoặc `pointer-events: none` để ngăn chặn chuột và phím Tab focus vào nút bấm hay ô kéo thả tệp.

---

## 4. Kế hoạch Kiểm thử & Xác minh (Verification Plan)

### 4.1 Unit & Integration Tests (`tests/workflow.test.ts`)
1. **Kiểm tra hiển thị overlay khi khởi động**:
   - Khi `loadApp` đang nạp, kiểm tra phần tử `.app-init-overlay` xuất hiện với nội dung *"Đang chuẩn bị ứng dụng, vui lòng chờ…"*.
2. **Kiểm tra tắt overlay khi hoàn tất**:
   - Khi tất cả Promise (`aiConfigGet`, `customRuleList`, `modelStatus`) resolved, kiểm tra `.app-init-overlay` bị gỡ bỏ khỏi DOM và màn hình chính sẵn sàng thao tác.
3. **Kiểm tra xử lý lỗi và nút "Thử lại"**:
   - Khi API khởi tạo bị reject, kiểm tra overlay hiển thị thông báo lỗi và nút `#retry-app-init`.
   - Click `#retry-app-init` kích hoạt lại quá trình nạp. Khi lần thử lại thành công, overlay biến mất.
4. **Kiểm tra tính an toàn của màn hình Cài đặt**:
   - Xác nhận người dùng không còn gặp tình trạng bấm vào Cài đặt khi hệ thống đang dở dang khởi tạo.

### 4.2 Hồi quy (Regression Testing)
- Chạy toàn bộ test suite hiện có: `npm test` trong `apps/desktop` (đảm bảo 59/59 tests hiện tại tiếp tục pass).
- Kiểm tra TypeScript build: `npm run build`.
