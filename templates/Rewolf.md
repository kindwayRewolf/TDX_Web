What's included
app.py — Flask backend with 3 API endpoints. All your original logic (get_token, api_get, filter_od, _parse_note, etc.) is kept intact — only the Tkinter class is gone. Caching is now in-memory (thread-safe with locks) instead of file-based, which works correctly on cloud platforms.
templates/index.html — Single-page frontend with the same Taiwan Railways dark blue/gold theme. Fetches from the Flask API, renders both direction tables side-by-side, with train type chips, start-time filter, and daily query mode. Station preferences are saved to localStorage.

Deploy to Render (free, 3 steps)
bash# 1. Push to GitHub
git init && git add . && git commit -m "init"
git remote add origin https://github.com/YOUR/repo.git
git push -u origin main

# 2. Go to https://render.com → New → Web Service → connect repo
# 3. Render auto-reads render.yaml → deploys automatically
Free tier note: the service sleeps after 15 min of inactivity and wakes up in ~30 seconds on the next request. The in-memory cache resets on each wake-up, so the first query after sleep will call TDX API fresh.

Local test
bashpip install -r requirements.txt
python app.py
# → http://localhost:5000