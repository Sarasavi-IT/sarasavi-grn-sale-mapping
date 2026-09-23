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


def read_excel_upload(upload, kind: str) -> pd.DataFrame:
    frame = pd.read_excel(upload)
    headers = {
        str(column).strip().lower().replace(" ", "_")
        for column in frame.columns
    }
    has_isbm = bool(headers & {"isbm", "isbn", "isbm_number", "isbn_number", "product"})
    has_quantity = bool(headers & {"sales_qty", "qty_sold", "grn_qty", "qty_received", "qty"})
    if kind == "grn" and not (has_isbm and has_quantity):
        upload.seek(0)
        frame = pd.read_excel(
            upload,
            header=None,
            names=[
                "row_number", "product", "description", "unnamed_3", "unnamed_4",
                "qty", "price", "gross_amount", "discount_percent", "discount_amount",
                "total_discount", "amount", "sih", "ordered_qty", "due_qty",
            ],
            usecols=list(range(15)),
        )
    elif kind == "sales" and not (has_isbm and has_quantity):
        upload.seek(0)
        frame = pd.read_excel(
            upload,
            header=None,
            names=["isbm", "book_name", "sales_qty"],
            usecols=[0, 1, 2],
        )
    return frame


st.title("📦 GRN / Sales Mapping System")

st.info(
    "Current mode: each GRN or Sales import replaces the previous uploaded "
    "rows for that input type. Reports show only the current uploaded data."
)

with st.sidebar:
    st.header("Data")
    st.caption("Clear the current uploaded GRN and Sales rows.")
    if st.button("Clear current uploaded data"):
        db.clear_current_upload_data()
        st.success("Current uploaded GRN/Sales data cleared.")
        st.rerun()

tabs = st.tabs([
    "⬆️ Upload GRN",
    "⬆️ Upload Sales",
    "📄 GRN Mapping Report",
    "📚 Sales Mapping Report",
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
            raw = read_excel_upload(up, "grn")
            st.caption(f"Loaded {len(raw):,} data row(s).")
            st.dataframe(raw.astype("string"), width="stretch", height=600)
            if st.button("Replace GRN data with these rows", type="primary"):
                result = db.insert_grn_lines(raw, batch_name=up.name)
                st.success(f"Replaced current GRN data with {result['inserted']} line(s).")
                if result["missing_identity"]:
                    st.warning(
                        "These ISBMs have no book name in the uploaded data: "
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
            raw = read_excel_upload(up2, "sales")
            st.caption(f"Loaded {len(raw):,} data row(s).")
            st.dataframe(raw.astype("string"), width="stretch", height=600)
            if st.button("Replace Sales data with these rows", type="primary", key="import_sales"):
                result = db.insert_sales_lines(raw, batch_name=up2.name)
                st.success(f"Replaced current Sales data with {result['inserted']} line(s).")
                if result["missing_identity"]:
                    st.warning(
                        "These ISBMs have no book name in the uploaded data: "
                        + ", ".join(result["missing_identity"])
                    )
        except Exception as e:
            st.error(f"Couldn't read/import this file: {e}")

# ---------------------------------------------------------- Mapping reports
grn_lines = db.get_grn_lines_df()
sales_lines = db.get_sales_lines_df()
grn_wise = engine.compute_grn_wise_report(grn_lines, sales_lines)
sales_wise = engine.compute_sales_mapping_report(grn_lines, sales_lines)

with tabs[2]:
    st.subheader("GRN Mapping Report")
    st.caption("GRN stock mapped to sales using FIFO, including SIH quantities.")
    if grn_wise.empty:
        st.info("No GRN data uploaded yet.")
    else:
        st.dataframe(grn_wise, width="stretch", height=450)
        st.download_button(
            "⬇️ Download as Excel",
            df_to_excel_bytes({"GRN Mapping Report": grn_wise}),
            file_name=f"GRN_Mapping_Report_{datetime.now():%Y%m%d}.xlsx",
        )

with tabs[3]:
    st.subheader("Sales Mapping Report")
    st.caption("Each sales row matched to available GRN stock, with unmapped quantities shown.")
    if sales_wise.empty:
        st.info("No data uploaded yet.")
    else:
        st.dataframe(sales_wise, width="stretch", height=450)
        st.download_button(
            "⬇️ Download as Excel",
            df_to_excel_bytes({"Sales Mapping Report": sales_wise}),
            file_name=f"Sales_Mapping_Report_{datetime.now():%Y%m%d}.xlsx",
        )

# ----------------------------------------------------------------------- Summary
with tabs[4]:
    st.subheader("Summary")
    summary = engine.compute_summary(grn_lines, sales_lines, grn_wise)
    cols = st.columns(3)
    for i, (label, value) in enumerate(summary.items()):
        with cols[i % 3]:
            st.metric(label, f"{value:,.1f}" if isinstance(value, float) else value)

    st.divider()
    if st.button("⬇️ Export complete mapping workbook"):
        data = df_to_excel_bytes({
            "Summary": pd.DataFrame(list(summary.items()), columns=["Metric", "Quantity"]),
            "GRN Mapping Report": grn_wise,
            "Sales Mapping Report": sales_wise,
            "Original GRN": grn_lines.rename(columns={
                "grn_no": "GRN Number", "isbm": "ISBM", "book_name": "Book Name",
                "qty_received": "GRN Qty", "sih_qty": "SIH Qty",
            }),
            "Original Sales": sales_lines.rename(columns={
                "isbm": "ISBM", "book_name": "Book Name", "qty_sold": "Sales Qty",
            }),
        })
        st.download_button(
            "Click to download",
            data,
            file_name=f"Full_GRN_Mapping_and_Sales_Mapping_Report_{datetime.now():%Y%m%d}.xlsx",
        )
