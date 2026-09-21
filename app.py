"""
GRN / Sales Mapping System — Streamlit app
Run with:  streamlit run app.py
"""

from io import BytesIO
from datetime import datetime

import pandas as pd
import streamlit as st

import database as db
import mapping_engine as engine

st.set_page_config(page_title="GRN / Sales Mapping System", layout="wide")
db.init_db()


def df_to_excel_bytes(sheets: dict) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name[:31], index=False)
    return buf.getvalue()


st.title("📦 GRN / Sales Mapping System")

st.info(
    "Current mode: each GRN or Sales import replaces the previous uploaded "
    "rows for that input type. Reports show only the current uploaded data."
)

with st.sidebar:
    st.header("Data")
    st.caption("Clear uploaded GRN/Sales rows. Identity master records are kept.")
    if st.button("Clear current uploaded data"):
        db.clear_current_upload_data()
        st.success("Current uploaded GRN/Sales data cleared.")
        st.rerun()

tabs = st.tabs([
    "⬆️ Upload GRN",
    "⬆️ Upload Sales",
    "🪪 Identity Master",
    "📄 GRN-wise Report",
    "📚 ISBM-wise Report",
    "📊 Summary",
])

# ----------------------------------------------------------------- Upload GRN
with tabs[0]:
    st.subheader("Upload a GRN file")
    st.caption(
        "Required columns: **GRN No, ISBM, GRN Qty**. If this file also has "
        "**GRN Date** / **Supplier** / **Book Name**, those refresh the master "
        "lists automatically. Importing replaces the previous GRN upload."
    )
    up = st.file_uploader("GRN Excel file", type=["xlsx", "xls"], key="grn_upload")
    if up is not None:
        try:
            raw = pd.read_excel(up)
            st.dataframe(raw.head(20), width="stretch")
            if st.button("Replace GRN data with these rows", type="primary"):
                result = db.insert_grn_lines(raw, batch_name=up.name)
                st.success(f"Replaced current GRN data with {result['inserted']} line(s).")
                if result["missing_identity"]:
                    st.warning(
                        "These ISBMs aren't in the Identity master yet — add "
                        "them on the Identity Master tab so future reports "
                        "show a proper Book Name: "
                        + ", ".join(result["missing_identity"])
                    )
        except Exception as e:
            st.error(f"Couldn't read/import this file: {e}")

# --------------------------------------------------------------- Upload Sales
with tabs[1]:
    st.subheader("Upload a Sales file")
    st.caption(
        "Required columns: **ISBM, Sales Qty**. Book Name is looked up "
        "automatically. Importing replaces the previous Sales upload."
    )
    up2 = st.file_uploader("Sales Excel file", type=["xlsx", "xls"], key="sales_upload")
    if up2 is not None:
        try:
            raw = pd.read_excel(up2)
            st.dataframe(raw.head(20), width="stretch")
            if st.button("Replace Sales data with these rows", type="primary", key="import_sales"):
                result = db.insert_sales_lines(raw, batch_name=up2.name)
                st.success(f"Replaced current Sales data with {result['inserted']} line(s).")
                if result["missing_identity"]:
                    st.warning(
                        "These ISBMs aren't in the Identity master yet: "
                        + ", ".join(result["missing_identity"])
                    )
        except Exception as e:
            st.error(f"Couldn't read/import this file: {e}")

# ------------------------------------------------------------- Identity Master
with tabs[2]:
    st.subheader("Identity master (ISBM → Book Name)")
    st.caption(
        "This is the single source of truth for book titles. Upload a file "
        "with ISBM + Book Name columns to bulk-add/update, or edit the table "
        "below directly."
    )
    up3 = st.file_uploader("Bulk upload (ISBM, Book Name)", type=["xlsx", "xls"], key="identity_upload")
    if up3 is not None:
        try:
            raw = pd.read_excel(up3)
            if st.button("Import into Identity master", type="primary"):
                n = db.upsert_identity(raw)
                st.success(f"Added/updated {n} identity record(s).")
        except Exception as e:
            st.error(f"Couldn't read/import this file: {e}")

    st.divider()
    st.markdown("**Current Identity master**")
    st.dataframe(db.get_identity_df(), width="stretch", height=350)

    st.markdown("**GRN master (GRN No → Date, Supplier)**")
    st.dataframe(db.get_grn_master_df(), width="stretch", height=250)

# ------------------------------------------------------------- GRN-wise Report
grn_lines = db.get_grn_lines_df()
sales_lines = db.get_sales_lines_df()
grn_wise = engine.compute_grn_wise_report(grn_lines, sales_lines)

with tabs[3]:
    st.subheader("GRN-wise Report")
    st.caption("Every GRN batch, how much of it has been sold (FIFO, oldest batch first), and what's left.")
    if grn_wise.empty:
        st.info("No GRN data uploaded yet.")
    else:
        st.dataframe(grn_wise, width="stretch", height=450)
        st.download_button(
            "⬇️ Download as Excel",
            df_to_excel_bytes({"GRN_Wise_Report": grn_wise}),
            file_name=f"GRN_Wise_Report_{datetime.now():%Y%m%d}.xlsx",
        )

# ------------------------------------------------------------ ISBM-wise Report
isbm_wise = engine.compute_isbm_wise_report(grn_lines, sales_lines, grn_wise)

with tabs[4]:
    st.subheader("ISBM-wise Report")
    st.caption("Total inventory movement per title, across all its GRN batches.")
    if isbm_wise.empty:
        st.info("No data uploaded yet.")
    else:
        st.dataframe(isbm_wise, width="stretch", height=450)
        st.download_button(
            "⬇️ Download as Excel",
            df_to_excel_bytes({"ISBM_Wise_Report": isbm_wise}),
            file_name=f"ISBM_Wise_Report_{datetime.now():%Y%m%d}.xlsx",
        )

# ----------------------------------------------------------------------- Summary
with tabs[5]:
    st.subheader("Summary")
    summary = engine.compute_summary(grn_lines, sales_lines, grn_wise)
    cols = st.columns(3)
    for i, (label, value) in enumerate(summary.items()):
        with cols[i % 3]:
            st.metric(label, f"{value:,.1f}" if isinstance(value, float) else value)

    st.divider()
    if st.button("⬇️ Export all three reports as one Excel file"):
        data = df_to_excel_bytes({
            "Summary": pd.DataFrame(list(summary.items()), columns=["Metric", "Value"]),
            "GRN_Wise_Report": grn_wise,
            "ISBM_Wise_Report": isbm_wise,
        })
        st.download_button(
            "Click to download",
            data,
            file_name=f"GRN_Sales_Reports_{datetime.now():%Y%m%d}.xlsx",
        )
