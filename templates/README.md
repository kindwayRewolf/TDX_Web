# 台鐵時刻表 — Web 版 (Flask + TDX API)

## 專案結構

```
tdx_web/
├── app.py              # Flask API + 所有 TDX 邏輯
├── templates/
│   └── index.html      # 單頁前端
├── requirements.txt
├── Procfile            # Render / Heroku 啟動命令
├── render.yaml         # Render 一鍵部署設定
└── README.md
```

## API 端點

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET  | `/` | 前端頁面 |
| GET  | `/api/stations` | 車站列表 |
| GET  | `/api/trains?from=0970&to=0990` | 常態班表（OD對） |
| GET  | `/api/trains/daily?from=0970&to=0990&date=2026-03-26` | 指定日期班次 |

## 本機測試

```bash
pip install -r requirements.txt
python app.py
# → http://localhost:5000
```

## 部署到 Render（免費）

1. 將此資料夾推到 GitHub repo
2. 前往 https://render.com → New → Web Service
3. 連結 GitHub repo
4. Render 會自動偵測 `render.yaml` 並部署
5. 免費方案：閒置 15 分鐘後休眠，有請求時自動喚醒（約 30 秒）

## 快取策略

| 資料 | 快取位置 | TTL |
|------|----------|-----|
| OAuth token | 記憶體 | token 到期前 30 秒更新 |
| 全量班表 | 記憶體 | 3 天 或 TDX ExpireDate |
| OD 篩選結果 | 記憶體 | 3 天 |
| 指定日期班表 | 記憶體 | 14 天（自動清理舊日期） |

> 免費方案服務重啟後快取清空，首次請求會重新向 TDX 取得資料。
