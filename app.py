"""
app.py — NL to SQL  |  SAP Business One Edition
Direct SQL Server connection — no file upload needed.
Connection credentials are stored in Streamlit secrets (.streamlit/secrets.toml).
"""

import streamlit as st
import pandas as pd
import plotly.express as px
from groq import Groq
import json
import re
from datetime import datetime

# ─────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SAP B1 — NL to SQL",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────
# LOAD SECRETS  (set in Streamlit Cloud → Settings → Secrets)
# ─────────────────────────────────────────────────────────────
try:
    GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
    DB_SERVER    = st.secrets["DB_SERVER"]      # e.g. "192.168.1.10" or "myserver.database.windows.net"
    DB_PORT      = st.secrets.get("DB_PORT", "1433")
    DB_NAME      = st.secrets["DB_NAME"]        # e.g. "SBODemoIN"
    DB_USER      = st.secrets["DB_USER"]        # e.g. "sa"
    DB_PASSWORD  = st.secrets["DB_PASSWORD"]
    DB_DRIVER    = st.secrets.get("DB_DRIVER", "ODBC Driver 18 for SQL Server")
    SECRETS_OK   = True
except Exception:
    SECRETS_OK   = False

TODAY = datetime.today().strftime("%Y-%m-%d")

# ─────────────────────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────────────────────
for key, default in {
    "question"     : "",
    "accuracy_log" : [],
    "schema_cache" : "",
    "db_connected" : False,
    "conn"         : None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ─────────────────────────────────────────────────────────────
# DATABASE CONNECTION  (SQL Server via pyodbc)
# ─────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner="Connecting to SQL Server...")
def get_connection(server, port, db, user, password, driver):
    """
    Returns a live pyodbc connection.
    Cached so reconnection only happens on secret change or app restart.
    """
    import pyodbc
    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server},{port};"
        f"DATABASE={db};"
        f"UID={user};"
        f"PWD={password};"
        "TrustServerCertificate=yes;"
        "Encrypt=yes;"
        "Connection Timeout=30;"
    )
    return pyodbc.connect(conn_str, autocommit=True)


@st.cache_data(show_spinner="Reading schema...", ttl=300)
def get_schema_sqlserver(_conn, db_name):
    """
    Reads table + column info from SQL Server information_schema.
    Returns a formatted schema string and list of table names.
    Cached for 5 minutes (ttl=300) to avoid repeated DB calls.
    """
    query = """
        SELECT
            t.TABLE_NAME,
            c.COLUMN_NAME,
            c.DATA_TYPE,
            c.CHARACTER_MAXIMUM_LENGTH,
            c.IS_NULLABLE
        FROM INFORMATION_SCHEMA.TABLES  t
        JOIN INFORMATION_SCHEMA.COLUMNS c
          ON t.TABLE_NAME = c.TABLE_NAME
         AND t.TABLE_SCHEMA = c.TABLE_SCHEMA
        WHERE t.TABLE_TYPE   = 'BASE TABLE'
          AND t.TABLE_SCHEMA = 'dbo'
        ORDER BY t.TABLE_NAME, c.ORDINAL_POSITION
    """
    df = pd.read_sql(query, _conn)

    schema_lines = []
    tables = []
    for tname, grp in df.groupby("TABLE_NAME", sort=False):
        tables.append(tname)
        col_parts = []
        for _, row in grp.iterrows():
            dtype = row["DATA_TYPE"]
            if row["CHARACTER_MAXIMUM_LENGTH"] and row["CHARACTER_MAXIMUM_LENGTH"] != -1:
                dtype += f"({int(row['CHARACTER_MAXIMUM_LENGTH'])})"
            null = "" if row["IS_NULLABLE"] == "YES" else " NOT NULL"
            col_parts.append(f"{row['COLUMN_NAME']} {dtype}{null}")

        # Row count per table
        try:
            cnt_df = pd.read_sql(
                f"SELECT COUNT(*) AS cnt FROM dbo.[{tname}]", _conn
            )
            count = int(cnt_df["cnt"].iloc[0])
        except Exception:
            count = "?"

        schema_lines.append(f"- {tname}({', '.join(col_parts)})  [{count} rows]")

    return "\n".join(schema_lines), tables


# ─────────────────────────────────────────────────────────────
# GROQ — GENERATE SQL
# ─────────────────────────────────────────────────────────────
def generate_sql(question: str, schema: str) -> str:
    client = Groq(api_key=GROQ_API_KEY)
    prompt = f"""You are a Microsoft SQL Server (T-SQL) expert working with an SAP Business One database.

Database schema:
{schema}

Date context:
- Today is {TODAY}
- "Last month" means the previous full calendar month
- Use GETDATE(), DATEADD(), DATEDIFF(), FORMAT() for date operations (T-SQL syntax)
- Use TOP instead of LIMIT
- Use CONVERT or CAST for type conversions
- Table names are in dbo schema, e.g. dbo.OINV

Write a T-SQL query to answer: "{question}"

Rules:
- Return ONLY the raw SQL query — no markdown, no backticks, no explanation
- Use proper T-SQL / SQL Server syntax
- Always alias tables for clarity (e.g. OINV inv, OCRD bp)
- Limit large result sets with TOP 100 unless the question asks for everything
"""
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=600,
        temperature=0.1,
    )
    raw = resp.choices[0].message.content.strip()
    # Strip any accidental markdown fences
    raw = re.sub(r"```sql|```tsql|```", "", raw, flags=re.IGNORECASE).strip()
    return raw


# ─────────────────────────────────────────────────────────────
# GROQ — SCORE ACCURACY
# ─────────────────────────────────────────────────────────────
def score_accuracy(question: str, sql: str, result_preview: str, schema: str) -> dict:
    client = Groq(api_key=GROQ_API_KEY)
    prompt = f"""You are a T-SQL quality evaluator for SAP Business One databases.

User question  : "{question}"
Generated SQL  : {sql}
Result preview : {result_preview}
Schema (excerpt): {schema[:3000]}

Score on 4 criteria (0–25 each):
1. Correctness       — Does the SQL logically answer the question?
2. Schema alignment  — Are the correct SAP B1 tables and columns used?
3. T-SQL quality     — Proper T-SQL syntax, aliases, efficient?
4. Result relevance  — Does the returned data look right for the question?

Return ONLY valid JSON — no markdown, no explanation:
{{"score":<0-100>,"correctness":<0-25>,"schema_alignment":<0-25>,"sql_quality":<0-25>,"result_relevance":<0-25>,"verdict":"<one line>"}}
"""
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300,
        temperature=0,
    )
    raw = re.sub(r"```json|```", "", resp.choices[0].message.content.strip()).strip()
    return json.loads(raw)


# ─────────────────────────────────────────────────────────────
# AUTO CHART
# ─────────────────────────────────────────────────────────────
def auto_chart(df: pd.DataFrame, question: str):
    if df is None or df.empty or len(df.columns) < 2:
        return None
    q        = question.lower()
    num_cols = df.select_dtypes(include="number").columns.tolist()
    cat_cols = df.select_dtypes(exclude="number").columns.tolist()
    if not num_cols:
        return None
    x_col = cat_cols[0] if cat_cols else df.columns[0]
    y_col = num_cols[0]

    if any(w in q for w in ["trend","monthly","daily","over time","by month","by date","year"]):
        fig = px.line(df, x=x_col, y=y_col, markers=True,
                      title=question, template="plotly_white")
    elif any(w in q for w in ["top","most","best","highest","ranking","selling","largest"]):
        df2 = df.sort_values(y_col, ascending=True).tail(20)
        fig = px.bar(df2, x=y_col, y=x_col, orientation="h",
                     title=question, template="plotly_white",
                     color=y_col, color_continuous_scale="Blues")
    elif any(w in q for w in ["category","city","status","distribution","breakdown","by","share"]):
        fig = (px.pie(df, names=x_col, values=y_col, title=question, template="plotly_white")
               if len(df) <= 12
               else px.bar(df, x=x_col, y=y_col, title=question, template="plotly_white",
                           color=y_col, color_continuous_scale="Teal"))
    else:
        fig = px.bar(df, x=x_col, y=y_col, title=question, template="plotly_white",
                     color=y_col, color_continuous_scale="Viridis")

    fig.update_layout(margin=dict(t=50, b=30), height=430)
    return fig


def score_badge(score: int) -> str:
    return "🟢" if score >= 85 else ("🟡" if score >= 60 else "🔴")


# ─────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/5/59/SAP_2011_logo.svg", width=80)
    st.markdown("## SAP B1 — NL to SQL")
    st.markdown("---")

    # ── Connection status ──
    if not SECRETS_OK:
        st.error("⚠️ Secrets not configured.\nSee setup instructions below.")
        conn = None
    else:
        try:
            conn = get_connection(DB_SERVER, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_DRIVER)
            st.success(f"🟢 Connected\n**{DB_NAME}** on `{DB_SERVER}`")
            st.session_state["db_connected"] = True
        except Exception as e:
            st.error(f"🔴 Connection failed:\n{e}")
            conn = None
            st.session_state["db_connected"] = False

    # ── DB info ──
    if conn:
        st.markdown(f"**Database:** `{DB_NAME}`")
        st.markdown(f"**Server:** `{DB_SERVER}:{DB_PORT}`")
        if st.button("🔄 Refresh Schema"):
            get_schema_sqlserver.clear()
            st.session_state["schema_cache"] = ""
            st.rerun()

    st.markdown("---")

    # ── Sample SAP B1 questions ──
    st.markdown("**💡 Sample SAP B1 Questions**")
    samples = [
        "Top 10 customers by AR invoice total this year",
        "Monthly sales revenue trend for last 6 months",
        "Total open AR invoices by customer",
        "Which items have stock below 10 units?",
        "All open purchase orders with supplier name",
        "Top 5 items by quantity sold last month",
        "Outstanding payments due this week",
        "Revenue by item group this year",
        "Overdue AR invoices older than 30 days",
        "Gross profit by sales person",
    ]
    for s in samples:
        if st.button(s, key=s, use_container_width=True):
            st.session_state["question"] = s

    st.markdown("---")

    # ── Accuracy history ──
    if st.session_state["accuracy_log"]:
        scores = [e["score"] for e in st.session_state["accuracy_log"]]
        avg    = sum(scores) / len(scores)
        st.metric("📊 Session Avg Accuracy", f"{avg:.1f} / 100")
        if st.button("🗑️ Clear History"):
            st.session_state["accuracy_log"] = []
            st.rerun()


# ─────────────────────────────────────────────────────────────
# MAIN AREA
# ─────────────────────────────────────────────────────────────
st.title("🔍 SAP Business One — Natural Language to SQL")
st.caption("Ask plain English questions about your SAP B1 database — powered by LLaMA 3.3 70B via Groq")

# ── Secrets setup instructions (shown when not configured) ──
if not SECRETS_OK:
    st.warning("### ⚙️ Setup Required")
    st.markdown("""
Your Streamlit secrets are not configured yet. Follow these steps:

**For Streamlit Cloud:**
1. Open your app on [share.streamlit.io](https://share.streamlit.io)
2. Go to **⋮ Menu → Settings → Secrets**
3. Paste the contents of `.streamlit/secrets.toml` (edit with your real values)
4. Click **Save** — the app will restart automatically

**For local development:**
- Create `.streamlit/secrets.toml` in your project folder with the values below
""")
    st.code("""
[secrets]
GROQ_API_KEY = "gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
DB_SERVER    = "192.168.1.10"          # your SQL Server IP or hostname
DB_PORT      = "1433"
DB_NAME      = "SBODemoIN"             # your SAP B1 company database name
DB_USER      = "sa"
DB_PASSWORD  = "YourPassword123"
DB_DRIVER    = "ODBC Driver 18 for SQL Server"
""", language="toml")
    st.stop()

# ── No connection ──
if not conn:
    st.error("Cannot connect to SQL Server. Check your secrets and that the server is reachable from Streamlit Cloud.")
    st.info("💡 Make sure your SQL Server allows inbound connections on port 1433 from external IPs, or use Azure SQL / a publicly reachable server.")
    st.stop()

# ─────────────────────────────────────────────────────────────
# LOAD SCHEMA (cached)
# ─────────────────────────────────────────────────────────────
if not st.session_state["schema_cache"]:
    with st.spinner("Reading database schema..."):
        try:
            schema, tables = get_schema_sqlserver(conn, DB_NAME)
            st.session_state["schema_cache"] = schema
        except Exception as e:
            st.error(f"Failed to read schema: {e}")
            st.stop()
else:
    schema = st.session_state["schema_cache"]

# ── Schema viewer ──
with st.expander(f"📋 Database Schema — `{DB_NAME}`", expanded=False):
    st.code(schema, language="sql")

st.markdown("---")

# ─────────────────────────────────────────────────────────────
# QUERY INPUT
# ─────────────────────────────────────────────────────────────
question = st.text_input(
    "💬 Ask your question in plain English",
    value=st.session_state.get("question", ""),
    placeholder="e.g. Top 10 customers by invoice total this year",
)

col1, col2, spacer = st.columns([1.2, 1.2, 6])
run      = col1.button("▶ Run Query",  type="primary",  use_container_width=True)
score_it = col2.button("🎯 Score It",  use_container_width=True,
                       help="Run + score accuracy of the generated SQL")

# ─────────────────────────────────────────────────────────────
# QUERY EXECUTION
# ─────────────────────────────────────────────────────────────
if (run or score_it) and question.strip():
    with st.spinner("Generating T-SQL with LLaMA 3.3 70B..."):
        try:
            sql = generate_sql(question, schema)
        except Exception as e:
            st.error(f"LLM error: {e}")
            st.stop()

    st.markdown("#### 🧾 Generated T-SQL")
    st.code(sql, language="sql")

    # ── Execute against SQL Server ──
    with st.spinner("Running query on SQL Server..."):
        try:
            df = pd.read_sql(sql, conn)
        except Exception as e:
            st.error(f"SQL execution error: {e}")
            st.markdown("👆 The query above failed. You can copy it, fix it in SSMS, and paste a corrected version.")
            st.stop()

    # ── Metrics ──
    m1, m2, m3 = st.columns(3)
    m1.metric("Rows returned", f"{len(df):,}")
    m2.metric("Columns",       len(df.columns))
    m3.metric("Database",      DB_NAME)

    # ── Tabs ──
    tab1, tab2, tab3 = st.tabs(["📄 Table", "📊 Chart", "🎯 Accuracy"])

    with tab1:
        st.dataframe(df, use_container_width=True, height=420)
        # Download button
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download CSV",
            data=csv,
            file_name=f"query_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
        )

    with tab2:
        fig = auto_chart(df, question)
        if fig:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No chart available — result needs at least one numeric column.")

    with tab3:
        if score_it:
            with st.spinner("Scoring with LLaMA..."):
                try:
                    preview = df.head(5).to_string()
                    result  = score_accuracy(question, sql, preview, schema)
                except Exception as e:
                    st.error(f"Scoring error: {e}")
                    result = None

            if result:
                total = result.get("score", 0)
                badge = score_badge(total)

                st.markdown(f"## {badge} Overall Score: **{total} / 100**")
                st.markdown(f"> _{result.get('verdict', '')}_")
                st.markdown("---")

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("✅ Correctness",       f"{result.get('correctness', 0)} / 25")
                c2.metric("🗂️ Schema Alignment",  f"{result.get('schema_alignment', 0)} / 25")
                c3.metric("⚙️ T-SQL Quality",     f"{result.get('sql_quality', 0)} / 25")
                c4.metric("📌 Result Relevance",  f"{result.get('result_relevance', 0)} / 25")

                st.session_state["accuracy_log"].append({
                    "question" : question,
                    "score"    : total,
                    "verdict"  : result.get("verdict", ""),
                })

                # Session history chart
                if len(st.session_state["accuracy_log"]) > 1:
                    st.markdown("---")
                    st.markdown("**📈 Score History This Session**")
                    log_df = pd.DataFrame(st.session_state["accuracy_log"])
                    log_df.index += 1
                    fig2 = px.bar(
                        log_df, x=log_df.index, y="score",
                        hover_data=["question", "verdict"],
                        color="score",
                        color_continuous_scale="RdYlGn",
                        range_color=[0, 100],
                        labels={"index": "Query #", "score": "Score"},
                        template="plotly_white",
                    )
                    fig2.add_hline(y=85, line_dash="dot", line_color="green",
                                   annotation_text="Good (85)")
                    fig2.add_hline(y=60, line_dash="dot", line_color="orange",
                                   annotation_text="Acceptable (60)")
                    fig2.update_layout(height=320)
                    st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("💡 Click **🎯 Score It** (instead of Run Query) to evaluate accuracy.")

# ─────────────────────────────────────────────────────────────
# FULL ACCURACY LOG (bottom of page)
# ─────────────────────────────────────────────────────────────
if st.session_state["accuracy_log"]:
    st.markdown("---")
    with st.expander("📋 Full Accuracy Log This Session"):
        log_df = pd.DataFrame(st.session_state["accuracy_log"])
        log_df.index += 1
        log_df.columns = ["Question", "Score", "Verdict"]
        st.dataframe(log_df, use_container_width=True)
