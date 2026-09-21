"""
mapping_engine.py
------------------
Pure functions: given the GRN lines and Sales lines (as DataFrames), work out
which sales consume which GRN batches on a FIFO basis (oldest GRN date first),
and build the three reports. No Excel row-position tricks involved, which is
what caused the row-2 self-reference bug in the old spreadsheet version.
"""

import pandas as pd


def compute_grn_wise_report(grn_df: pd.DataFrame, sales_df: pd.DataFrame) -> pd.DataFrame:
    if grn_df.empty:
        return grn_df.assign(mapped_qty=[], balance=[], status=[])

    total_sold_by_isbm = sales_df.groupby("isbm")["qty_sold"].sum().to_dict()
    remaining_sales = dict(total_sold_by_isbm)  # will be drawn down FIFO

    df = grn_df.copy()
    df["_date_sort"] = pd.to_datetime(df["grn_date"], dayfirst=True, errors="coerce")
    # Oldest GRN first within each ISBM; undated GRNs sort last so they don't
    # jump the queue ahead of dated ones.
    df = df.sort_values(["isbm", "_date_sort", "grn_no"], na_position="last").reset_index(drop=True)

    mapped_qty, balance, status = [], [], []
    for _, row in df.iterrows():
        isbm = row["isbm"]
        qty_received = float(row["qty_received"])
        available = max(remaining_sales.get(isbm, 0.0), 0.0)
        mapped = min(qty_received, available)
        remaining_sales[isbm] = available - mapped
        bal = qty_received - mapped
        mapped_qty.append(mapped)
        balance.append(bal)
        if bal == 0:
            status.append("FULL")
        elif mapped > 0:
            status.append("PARTIAL")
        else:
            status.append("UNSOLD")

    df["mapped_qty"] = mapped_qty
    df["balance"] = balance
    df["status"] = status
    return df.drop(columns=["_date_sort"])


def compute_isbm_wise_report(grn_df: pd.DataFrame, sales_df: pd.DataFrame,
                              grn_wise_df: pd.DataFrame) -> pd.DataFrame:
    all_isbm = pd.Index(pd.concat([grn_df["isbm"], sales_df["isbm"]]).unique(), name="isbm")

    total_received = grn_df.groupby("isbm")["qty_received"].sum()
    total_sold = sales_df.groupby("isbm")["qty_sold"].sum()
    mapped = grn_wise_df.groupby("isbm")["mapped_qty"].sum() if not grn_wise_df.empty else pd.Series(dtype=float)

    out = pd.DataFrame(index=all_isbm)
    out["total_received"] = total_received
    out["total_sold"] = total_sold
    out["mapped_qty"] = mapped
    out = out.fillna(0.0)
    out["unmapped_sales_balance"] = (out["total_sold"] - out["mapped_qty"]).clip(lower=0)
    out["stock_balance"] = out["total_received"] - out["mapped_qty"]

    def _status(r):
        if r["total_received"] == 0 and r["total_sold"] > 0:
            return "UNMAPPED SALES (no GRN on file)"
        if r["unmapped_sales_balance"] > 0:
            return "PARTIAL"
        if r["total_sold"] == 0:
            return "UNSOLD"
        return "FULLY RECONCILED"

    out["reconciliation_status"] = out.apply(_status, axis=1)

    # Attach book names
    names = pd.concat([
        grn_df[["isbm", "book_name"]] if "book_name" in grn_df.columns else pd.DataFrame(columns=["isbm", "book_name"]),
        sales_df[["isbm", "book_name"]] if "book_name" in sales_df.columns else pd.DataFrame(columns=["isbm", "book_name"]),
    ]).drop_duplicates(subset="isbm").set_index("isbm")["book_name"]
    out.insert(0, "book_name", names)
    return out.reset_index()


def compute_summary(grn_df: pd.DataFrame, sales_df: pd.DataFrame,
                     grn_wise_df: pd.DataFrame) -> dict:
    total_received = grn_df["qty_received"].sum() if not grn_df.empty else 0
    total_sold = sales_df["qty_sold"].sum() if not sales_df.empty else 0
    mapped = grn_wise_df["mapped_qty"].sum() if not grn_wise_df.empty else 0
    unmapped = total_sold - mapped
    balance = total_received - mapped
    sell_through = (mapped / total_received * 100) if total_received else 0
    return {
        "Total GRN Qty Received": total_received,
        "Total Sales Qty": total_sold,
        "Mapped Sales Qty": mapped,
        "Unmapped Sales Balance": unmapped,
        "GRN Balance Qty (unsold stock)": balance,
        "Sell-Through Rate (%)": round(sell_through, 1),
    }
