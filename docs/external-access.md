# 外網連線方案

## 已採用的預設方式

這個專案現在支援兩種啟動方式：

```powershell
.\run-lan.ps1
```

給同一個 Wi-Fi / 區網內的手機或電腦連線。

```powershell
.\run-public-tunnel.ps1
```

用 Cloudflare Quick Tunnel 產生一個臨時 HTTPS 外網網址。

## 安全設定

`.env` 內的 `APP_ACCESS_TOKEN` 只要不是空值，就會啟用登入保護。外網公開時不要關掉這個值，因為這個工具可以下載影片、讀取輸出檔，也可能消耗 OpenAI API 額度。

你可以自己設定 token：

```powershell
.\set-token.ps1 -Restart
```

互動輸入時 token 不會顯示在畫面上。若用命令參數也可以：

```powershell
.\set-token.ps1 -Token "my-private-token" -Restart
```

但命令參數可能留下 shell history，建議用互動輸入。

手機 App 或其他 API client 可以用：

```http
Authorization: Bearer <APP_ACCESS_TOKEN>
```

或：

```http
X-Access-Token: <APP_ACCESS_TOKEN>
```

## 長期正式部署

Quick Tunnel 適合測試。正式給外部長期使用時，建議改成：

- Cloudflare Tunnel 綁定自己的網域
- 或部署到 VPS，前面放 Caddy / Nginx HTTPS reverse proxy
- 背後服務仍然只聽 `127.0.0.1`
- 加上正式帳號系統、任務佇列、磁碟容量限制與使用者配額
