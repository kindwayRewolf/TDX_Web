py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt


Create a .env with
TDX_CLIENT_ID=your_client_id
TDX_CLIENT_SECRET=your_client_secret

start Flask:
.\.venv\Scripts\python.exe app.py

Then open http://127.0.0.1:5000. You can check that the server is responding at http://127.0.0.1:5000/health.