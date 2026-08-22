# 手機 App 化規劃

## 建議方向

先保留目前 FastAPI 後端，手機端只做操作介面。影片下載、轉錄、翻譯、摘要、分析、剪輯都放在後端執行，手機 App 負責送 URL、看進度、下載結果。

## App 技術選型

建議用 React Native / Expo：

- 一套程式可同時支援 iOS 與 Android
- 很適合做表單、任務進度、結果列表、影片預覽與下載
- 可以直接呼叫目前已存在的 API

如果之後只做 iPhone 且要深度整合 iOS 分享選單，再改 SwiftUI。

## 第一版功能

1. 登入畫面：輸入 server URL 與 access token。
2. 新增任務：貼 YouTube URL，設定精華段數與片段秒數。
3. 任務列表：顯示 queued / running / completed / failed。
4. 任務詳情：顯示摘要、分析、逐字稿、精華片段。
5. 片段播放：直接播放後端輸出的 `clips/*.mp4`。
6. 分享：把摘要、逐字稿或影片片段分享出去。

## 後端需要補強

目前後端已經有基本 API。正式 App 化前建議再補：

- 任務取消 API
- 任務刪除 / 清理輸出 API
- 使用者帳號與多使用者隔離
- 任務佇列改成 Redis Queue / Celery / Dramatiq
- WebSocket 或 Server-Sent Events 進度推送
- 輸出檔過期清理
- 上傳到雲端儲存，例如 S3 / R2

## API 對應

- `POST /api/login`：網頁登入用
- `POST /api/jobs`：建立分析任務
- `GET /api/jobs`：任務列表
- `GET /api/jobs/{job_id}`：任務狀態與結果
- `GET /api/jobs/{job_id}/files/{path}`：下載逐字稿、摘要、分析或影片片段

手機 App 可改用 `Authorization: Bearer <APP_ACCESS_TOKEN>` 呼叫 API，不一定要走 cookie 登入。

