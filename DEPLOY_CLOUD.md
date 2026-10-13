# 股跡｜部署雲端版本（Render）

這是從原本本機版升級的 **單一使用者私有雲端版**。Windows 原本啟動方式不變。

## 先了解費用與限制

本方案用 Render **Starter 付費 Web Service** 與持久化 Disk，並非免費。以 Render 實際結帳畫面為準。免費 Web Service **沒有持久化 Disk**；即使能啟動網站，也會在重啟或重新部署後失去 SQLite 紀錄，所以請勿將免費方案用於正式記帳。

## 上線步驟

1. 在 GitHub 建立 **私人 repository**，例如 `tw-portfolio-studio`。將這個資料夾的程式碼上傳至 repository **根目錄**（確認 GitHub 頂層看得到 `app.py` 和 `render.yaml`）。不要上傳 `portfolio.sqlite3`、帳號或密碼。
2. 前往 https://dashboard.render.com/，使用 GitHub 帳號登入並授權你的 repo。
3. 選擇 `New` → `Blueprint`，選取此 repository，確認服務內容及費用。
4. 初次建立時，Render 會要求輸入這些變數：
   - `APP_USER`：自行設定的登入名稱（例如自己的暱稱）。
   - `APP_PASSWORD`：至少 12 個字元的**強密碼**，不要與其他服務共用。
   - `FINMIND_TOKEN`：可填入自己的 FinMind Token，沒有則先留空（如 Render 要求可後續到 Environment 填入）。
5. 確認部署成功後，用 Render 提供的 `https://...onrender.com` 網址打開網站，瀏覽器會跳出登入框。填入剛才的帳號與密碼。
6. 在「出入金」新增本金、再輸入買賣紀錄，更新行情。使用手機以同一個 HTTPS 網址和帳密開啟即可。

> **不能直接把本機資料一起搬過去**：既有 `portfolio.sqlite3` 留在舊電腦內，雲端使用新的空資料庫。可從原本網站的交易紀錄以標準 CSV 格式整理後再匯入雲端。出入金、手動收盤價也須重新輸入；本版尚未提供完整資料庫搬移工具。

## 隱私與資料安全

- 網站每個頁面與 API 都需要 HTTP Basic 登入；Render 網址請使用 **HTTPS**。這是簡單的單人登入，沒有註冊、找回密碼、兩步驟驗證、多帳戶隔離。
- 請勿與他人共用帳密，也別在公開 GitHub repo 放交易紀錄、環境變數、Token 或 SQLite 檔案。
- SQLite 會寫入 `/data/portfolio.sqlite3`，該路徑映射到 Render Persistent Disk，重新部署可保留。**持久化不是備份**：仍建議定期備份資料庫與重要交易 CSV。
- 此版沒有內建自動盤後排程，只能手動點「更新台股行情」；未來可加排程工作。
- 單一台服務與持久化磁碟綁定；不要同時啟動多份部署共用此 SQLite 檔案。
- Render 套餐、區域和持久化磁碟規格可能變動，如 Blueprint 顯示不支援 `region: singapore`，可移除 region 那行後重新部署。

## 本機仍可照舊使用

Windows 雙擊 `start_windows.bat`；本機沒設 `CLOUD_MODE=1` 時，不會強制登入。若需要本機登入，可設定 `APP_USER` 與 `APP_PASSWORD` 環境變數。

## 注意

你需要自行登入 GitHub 與 Render，並由你確認可能的服務費用；目前交付的是**可部署程式碼**，還不是已公開可連線的網站。
