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
        return pd.DataFrame(columns=[
            "grn_no", "isbm", "book_name", "qty_received", "sih_qty",
            "grn_plus_sih_qty", "sales_qty", "balance_qty", "mapping_status",
        ])

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
        sih_qty = float(row.get("sih_qty", 0) or 0)
        available_qty = qty_received + sih_qty
        available = max(remaining_sales.get(isbm, 0.0), 0.0)
        mapped = min(available_qty, available)
        remaining_sales[isbm] = available - mapped
        bal = available_qty - mapped
        mapped_qty.append(mapped)
        balance.append(bal)
        total_available = grn_df.loc[grn_df["isbm"] == isbm, "qty_received"].sum()
        total_available += grn_df.loc[grn_df["isbm"] == isbm, "sih_qty"].sum() if "sih_qty" in grn_df else 0
        if total_sold_by_isbm.get(isbm, 0) > total_available:
            status.append("Sales > Available")
        elif abs(bal) < 1e-9:
            status.append("TALLY")
        else:
            status.append("Balance Available")

    df["grn_plus_sih_qty"] = df["qty_received"] + df.get("sih_qty", 0)
    df["sales_qty"] = mapped_qty
    df["balance_qty"] = balance
    df["mapping_status"] = status
    return df[[
        "grn_no", "isbm", "book_name", "qty_received", "sih_qty",
        "grn_plus_sih_qty", "sales_qty", "balance_qty", "mapping_status",
    ]].rename(columns={
        "grn_no": "GRN Number", "isbm": "ISBM", "book_name": "Book Name",
        "qty_received": "GRN Qty", "sih_qty": "SIH Qty",
        "grn_plus_sih_qty": "GRN + SIH Qty", "sales_qty": "Sales Qty",
        "balance_qty": "Balance Qty", "mapping_status": "Mapping Status",
    })


def compute_sales_mapping_report(grn_df: pd.DataFrame, sales_df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "ISBM", "Book Name", "Sales Qty", "Mapped Qty", "Unmapped Qty",
        "GRN Number", "GRN Qty", "SIH Qty", "Mapping Status",
    ]
    if sales_df.empty:
        return pd.DataFrame(columns=columns)

    grn = grn_df.copy()
    if "sih_qty" not in grn:
        grn["sih_qty"] = 0.0
    grn["_date_sort"] = pd.to_datetime(grn["grn_date"], dayfirst=True, errors="coerce")
    grn = grn.sort_values(["isbm", "_date_sort", "grn_no"], na_position="last")
    remaining = {
        isbm: [float(row.qty_received) + float(row.sih_qty or 0)
               for row in group.itertuples()]
        for isbm, group in grn.groupby("isbm", sort=False)
    }
    batches = {
        isbm: list(group.itertuples())
        for isbm, group in grn.groupby("isbm", sort=False)
    }
    output = []
    for row in sales_df.itertuples(index=False):
        isbm = row.isbm
        sales_qty = float(row.qty_sold)
        mapped = 0.0
        first_batch = None
        for index, available in enumerate(remaining.get(isbm, [])):
            allocation = min(max(available, 0.0), sales_qty - mapped)
            if allocation > 0 and first_batch is None:
                first_batch = batches[isbm][index]
            remaining[isbm][index] -= allocation
            mapped += allocation
            if mapped >= sales_qty:
                break
        unmapped = sales_qty - mapped
        output.append({
            "ISBM": isbm,
            "Book Name": row.book_name,
            "Sales Qty": sales_qty,
            "Mapped Qty": mapped,
            "Unmapped Qty": unmapped,
            "GRN Number": first_batch.grn_no if first_batch else None,
            "GRN Qty": first_batch.qty_received if first_batch else None,
            "SIH Qty": first_batch.sih_qty if first_batch else None,
            "Mapping Status": (
                "Not in GRN" if first_batch is None
                else "Fully Mapped" if unmapped == 0
                else "Partially Mapped"
            ),
        })
    return pd.DataFrame(output, columns=columns)


def compute_isbm_wise_report(grn_df: pd.DataFrame, sales_df: pd.DataFrame,
                              grn_wise_df: pd.DataFrame) -> pd.DataFrame:
    all_isbm = pd.Index(pd.concat([grn_df["isbm"], sales_df["isbm"]]).unique(), name="isbm")

    total_received = grn_df.groupby("isbm")["qty_received"].sum()
    total_sold = sales_df.groupby("isbm")["qty_sold"].sum()
    mapped = grn_wise_df.groupby("ISBM")["Sales Qty"].sum() if not grn_wise_df.empty else pd.Series(dtype=float)

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
    grn_isbm_count = grn_df["isbm"].nunique() if not grn_df.empty else 0
    total_sold = sales_df["qty_sold"].sum() if not sales_df.empty else 0
    mapped = grn_wise_df["Sales Qty"].sum() if not grn_wise_df.empty else 0
    unmapped = total_sold - mapped
    sih = grn_df["sih_qty"].sum() if "sih_qty" in grn_df else 0
    available = total_received + sih
    return {
        "GRN ISBM Count": grn_isbm_count,
        "GRN Qty": total_received,
        "SIH Qty": sih,
        "GRN + SIH Qty": available,
        "Total Sales Qty": total_sold,
        "Mapped Sales Qty": mapped,
        "Unmapped Sales Qty": unmapped,
    }
