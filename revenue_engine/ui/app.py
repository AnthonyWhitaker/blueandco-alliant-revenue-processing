"""
Alliant Revenue Processing Engine — Streamlit App Entry Point.

Run with:
    streamlit run ui/app.py
from the revenue_engine/ directory.
"""

import sys
from pathlib import Path

import streamlit as st

# Add parent to path so engine imports work
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.db import init_db
from core.misc import get_dashboard_stats

st.set_page_config(
    page_title="Alliant Revenue Processing",
    page_icon="💲",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize DB on first run
@st.cache_resource
def get_db():
    return init_db()

conn = get_db()
st.session_state["conn"] = conn

# Sidebar navigation note
st.sidebar.title("Alliant Revenue Processing")
st.sidebar.markdown("---")
st.sidebar.markdown(
    """
    **Navigation**
    - 📁 **Upload** — Upload and process a supplier file
    - 🔍 **Review** — Resolve unmatched customers
    - 📊 **Export** — Download processed output files
    - ⚙️ **Admin** — Manage suppliers, members, aliases
    """
)
st.sidebar.markdown("---")
st.sidebar.caption("v1.0 — Alliant Purchasing Solutions")

# Home page
st.title("Alliant Revenue Processing Engine")
st.markdown(
    """
    Welcome to the **common revenue reconciliation engine** for Alliant supplier files.

    This tool processes raw supplier remittance files, matches customers to the
    Alliant member database, aggregates revenue by entity code and earned period,
    and produces Salesforce-ready upload files — for any configured supplier.

    ---

    ### Getting Started

    1. **First time?** Go to **Admin → Members** and load your `Account List.xlsx`
    2. Import your `Ledger.xlsx` in **Admin → Payment Ledger**
    3. Verify your supplier profiles in **Admin → Suppliers**
    4. Then use **Upload** to process a new supplier file

    ---
    """
)

# Quick stats
col1, col2, col3, col4 = st.columns(4)
try:
    stats = get_dashboard_stats(conn)
    col1.metric("Members Loaded", f"{stats.active_members:,}")
    col2.metric("Suppliers Configured", stats.suppliers)
    col3.metric("Jobs Processed", stats.jobs)
    col4.metric("Pending Reviews", stats.pending_reviews, delta_color="inverse")
except Exception:
    col1.metric("Members Loaded", "—")
    col2.metric("Suppliers Configured", "—")
    col3.metric("Jobs Processed", "—")
    col4.metric("Pending Reviews", "—")
