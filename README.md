# 🔍 SAP Business One — Natural Language to SQL

Ask plain English questions about your live SAP B1 SQL Server database.  
Powered by **LLaMA 3.3 70B** via Groq. No file uploads — direct DB connection.

---

## ✨ Features

- 🔌 Direct SQL Server connection — no file upload needed
- 💬 Plain English → T-SQL (generated for SQL Server, not SQLite)
- 📊 Auto charts: bar, line, pie based on query context
- 🎯 Accuracy scoring — LLaMA rates every query 0–100
- ⬇️ Download results as CSV
- 🔒 Credentials stored in Streamlit secrets — never in code

---

## 🗂️ File Structure

```
nl-to-sql-app/
├── app.py                      # Main Streamlit app
├── generate_sap_b1_bak.py      # Generate SAP B1 demo .bak file
├── generate_sap_b1_demo.py     # Generate SAP B1 demo SQLite DB
├── requirements.txt            # Python dependencies
├── packages.txt                # System packages (ODBC driver for Streamlit Cloud)
├── .gitignore                  # secrets.toml excluded
├── .streamlit/
│   └── secrets.toml            # ← YOUR CREDENTIALS (never commit this)
└── README.md
```

---

## ☁️ Deploying on Streamlit Cloud

### Step 1 — Push to GitHub
```bash
git init
git add .
git commit -m "initial commit"
git remote add origin https://github.com/YOUR_USERNAME/nl-to-sql-app.git
git push -u origin main
```

### Step 2 — Deploy
1. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**
2. Select your repo → branch `main` → main file `app.py`
3. Click **Deploy**

### Step 3 — Add Secrets
1. In your deployed app: **⋮ → Settings → Secrets**
2. Paste this (with your real values):

```toml
GROQ_API_KEY = "gsk_..."
DB_SERVER    = "your-server-ip-or-hostname"
DB_PORT      = "1433"
DB_NAME      = "SBODemoIN"
DB_USER      = "sa"
DB_PASSWORD  = "YourPassword"
DB_DRIVER    = "ODBC Driver 18 for SQL Server"
```

3. Click **Save** — app restarts and connects automatically

---

## ⚠️ SQL Server Accessibility

Streamlit Cloud runs on shared cloud infrastructure. Your SQL Server **must be reachable from the internet** on port 1433:

| Setup | How to make it accessible |
|-------|--------------------------|
| On-premise server | Open port 1433 in firewall + use static IP |
| Azure SQL | Already public — use `.database.windows.net` hostname |
| AWS RDS | Set publicly accessible = yes, open security group |
| Local machine | Use a tunnel like [ngrok](https://ngrok.com) or [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) |

> 💡 **Recommended:** Create a dedicated read-only SQL login for this app — never use `sa` in production.

---

## 🎯 Accuracy Scoring

Click **🎯 Score It** to evaluate any query:

| Dimension | Max |
|-----------|-----|
| ✅ Correctness — answers the question? | 25 |
| 🗂️ Schema Alignment — right SAP B1 tables? | 25 |
| ⚙️ T-SQL Quality — proper syntax, aliases? | 25 |
| 📌 Result Relevance — data looks right? | 25 |

🟢 85–100 &nbsp;&nbsp; 🟡 60–84 &nbsp;&nbsp; 🔴 0–59

---

## ⚙️ Tech Stack

- [Streamlit](https://streamlit.io) — UI
- [Groq + LLaMA 3.3 70B](https://groq.com) — SQL generation & scoring
- [pyodbc](https://github.com/mkleehammer/pyodbc) — SQL Server connection
- [Plotly](https://plotly.com) — charts
- [Pandas](https://pandas.pydata.org) — data handling
