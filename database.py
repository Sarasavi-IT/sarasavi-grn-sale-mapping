"""
database.py
-----------
All persistence for the GRN / Sales mapping system lives here, in a single
SQLite file (grn_system.db, created automatically next to this script).

Tables
------
identity_master   ISBM  -> Book Name                (the "identity record")
grn_master        GRN No -> GRN Date, Supplier       (one row per delivery note)
grn_lines         current uploaded GRN lines
sales_lines       current uploaded sales lines

Uploading a new GRN/Sales file replaces the previous transaction rows, so
reports only show the latest uploaded input data. The identity master remains
as reusable reference data for book names.
"""

import sqlite3
import pandas as pd
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "grn_system.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS identity_master (
            isbm TEXT PRIMARY KEY,
            book_name TEXT
        );

        CREATE TABLE IF NOT EXISTS grn_master (
            grn_no TEXT PRIMARY KEY,
            grn_date TEXT,
            supplier TEXT
        );

        CREATE TABLE IF NOT EXISTS grn_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grn_no TEXT NOT NULL,
            isbm TEXT NOT NULL,
            qty_received REAL NOT NULL,
            upload_batch TEXT,
            uploaded_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sales_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            isbm TEXT NOT NULL,
            qty_sold REAL NOT NULL,
            upload_batch TEXT,
            uploaded_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column headers: strip whitespace, lowercase, underscores."""
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return df


def _find_col(df: pd.DataFrame, *candidates) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def clear_grn_upload_data() -> None:
    """Remove current GRN input rows and their GRN header details."""
    conn = get_conn()
    conn.execute("DELETE FROM grn_lines")
    conn.execute("DELETE FROM grn_master")
    conn.commit()
    conn.close()


def clear_sales_upload_data() -> None:
    """Remove current sales input rows."""
    conn = get_conn()
    conn.execute("DELETE FROM sales_lines")
    conn.commit()
    conn.close()


def clear_current_upload_data() -> None:
    """Remove all current transaction input rows without clearing identities."""
    conn = get_conn()
    conn.execute("DELETE FROM grn_lines")
    conn.execute("DELETE FROM sales_lines")
    conn.execute("DELETE FROM grn_master")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- identity
def upsert_identity(df: pd.DataFrame) -> int:
    """Add/update ISBM -> Book Name pairs. Returns rows written."""
    df = _norm_cols(df)
    isbm_col = _find_col(df, "isbm", "isbn", "isbm_number", "isbn_number")
    name_col = _find_col(df, "book_name", "item_description", "description", "title")
    if not isbm_col or not name_col:
        raise ValueError(
            "Identity file needs an ISBM/ISBN column and a Book Name column."
        )
    conn = get_conn()
    rows = 0
    for _, r in df.iterrows():
        isbm = str(r[isbm_col]).strip()
        name = str(r[name_col]).strip()
        if not isbm or isbm.lower() == "nan":
            continue
        conn.execute(
            """INSERT INTO identity_master (isbm, book_name) VALUES (?, ?)
               ON CONFLICT(isbm) DO UPDATE SET book_name=excluded.book_name""",
            (isbm, name),
        )
        rows += 1
    conn.commit()
    conn.close()
    return rows


def lookup_book_name(isbm: str) -> str:
    conn = get_conn()
    row = conn.execute(
        "SELECT book_name FROM identity_master WHERE isbm = ?", (isbm,)
    ).fetchone()
    conn.close()
    return row[0] if row else "UNKNOWN (add to Identity master)"


# ---------------------------------------------------------------- grn master
def upsert_grn_master(df: pd.DataFrame) -> int:
    df = _norm_cols(df)
    grn_col = _find_col(df, "grn_no", "grn_number")
    date_col = _find_col(df, "grn_date", "date")
    sup_col = _find_col(df, "supplier", "supplier_name")
    if not grn_col:
        raise ValueError("GRN master needs a GRN No column.")
    conn = get_conn()
    rows = 0
    for _, r in df.iterrows():
        grn_no = str(r[grn_col]).strip()
        if not grn_no or grn_no.lower() == "nan":
            continue
        grn_date = str(r[date_col]).strip() if date_col and pd.notna(r.get(date_col)) else None
        supplier = str(r[sup_col]).strip() if sup_col and pd.notna(r.get(sup_col)) else None
        conn.execute(
            """INSERT INTO grn_master (grn_no, grn_date, supplier) VALUES (?, ?, ?)
               ON CONFLICT(grn_no) DO UPDATE SET
                 grn_date=COALESCE(excluded.grn_date, grn_master.grn_date),
                 supplier=COALESCE(excluded.supplier, grn_master.supplier)""",
            (grn_no, grn_date, supplier),
        )
        rows += 1
    conn.commit()
    conn.close()
    return rows


def lookup_grn_header(grn_no: str):
    conn = get_conn()
    row = conn.execute(
        "SELECT grn_date, supplier FROM grn_master WHERE grn_no = ?", (grn_no,)
    ).fetchone()
    conn.close()
    if row:
        return row[0], row[1]
    return None, None


# ---------------------------------------------------------------- grn lines
def insert_grn_lines(df: pd.DataFrame, batch_name: str, replace_existing: bool = True) -> dict:
    """
    Accepts a raw uploaded GRN file. Required: GRN No, ISBM, GRN Qty.
    Optional in the same file: GRN Date, Supplier, Book Name (these seed/refresh
    the masters). Missing Book Name / Date / Supplier are auto-filled from the
    identity_master / grn_master tables.
    """
    df = _norm_cols(df)
    grn_col = _find_col(df, "grn_no", "grn_number")
    isbm_col = _find_col(df, "isbm", "isbn", "isbm_number")
    qty_col = _find_col(df, "grn_qty", "qty_received", "qty")
    if not (grn_col and isbm_col and qty_col):
        raise ValueError(
            "GRN file needs at minimum: GRN No, ISBM, GRN Qty columns."
        )

    if replace_existing:
        clear_grn_upload_data()

    # Seed masters from anything this file happens to include.
    name_col = _find_col(df, "book_name", "item_description", "description")
    if name_col:
        upsert_identity(df.rename(columns={isbm_col: "isbm", name_col: "book_name"}))
    if _find_col(df, "grn_date", "date") or _find_col(df, "supplier"):
        upsert_grn_master(df)

    conn = get_conn()
    now = datetime.now().isoformat(timespec="seconds")
    inserted, missing_identity = 0, set()
    for _, r in df.iterrows():
        grn_no = str(r[grn_col]).strip()
        isbm = str(r[isbm_col]).strip()
        qty = r[qty_col]
        if not grn_no or not isbm or pd.isna(qty):
            continue
        if lookup_book_name(isbm).startswith("UNKNOWN"):
            missing_identity.add(isbm)
        conn.execute(
            """INSERT INTO grn_lines (grn_no, isbm, qty_received, upload_batch, uploaded_at)
               VALUES (?, ?, ?, ?, ?)""",
            (grn_no, isbm, float(qty), batch_name, now),
        )
        inserted += 1
    conn.commit()
    conn.close()
    return {"inserted": inserted, "missing_identity": sorted(missing_identity)}


# ---------------------------------------------------------------- sales lines
def insert_sales_lines(df: pd.DataFrame, batch_name: str, replace_existing: bool = True) -> dict:
    """Accepts a raw uploaded Sales file. Required: ISBM, Sales Qty."""
    df = _norm_cols(df)
    isbm_col = _find_col(df, "isbm", "isbn", "isbm_number")
    qty_col = _find_col(df, "sales_qty", "qty_sold", "qty")
    if not (isbm_col and qty_col):
        raise ValueError("Sales file needs at minimum: ISBM, Sales Qty columns.")

    if replace_existing:
        clear_sales_upload_data()

    name_col = _find_col(df, "book_name", "item_description", "description")
    if name_col:
        upsert_identity(df.rename(columns={isbm_col: "isbm", name_col: "book_name"}))

    conn = get_conn()
    now = datetime.now().isoformat(timespec="seconds")
    inserted, missing_identity = 0, set()
    for _, r in df.iterrows():
        isbm = str(r[isbm_col]).strip()
        qty = r[qty_col]
        if not isbm or pd.isna(qty):
            continue
        if lookup_book_name(isbm).startswith("UNKNOWN"):
            missing_identity.add(isbm)
        conn.execute(
            """INSERT INTO sales_lines (isbm, qty_sold, upload_batch, uploaded_at)
               VALUES (?, ?, ?, ?)""",
            (isbm, float(qty), batch_name, now),
        )
        inserted += 1
    conn.commit()
    conn.close()
    return {"inserted": inserted, "missing_identity": sorted(missing_identity)}


# ---------------------------------------------------------------- readers
def get_identity_df() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM identity_master ORDER BY isbm", conn)
    conn.close()
    return df


def get_grn_master_df() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM grn_master ORDER BY grn_no", conn)
    conn.close()
    return df


def get_grn_lines_df() -> pd.DataFrame:
    conn = get_conn()
    query = """
        SELECT gl.grn_no, gm.grn_date, gm.supplier, gl.isbm,
               im.book_name, gl.qty_received
        FROM grn_lines gl
        LEFT JOIN grn_master gm ON gl.grn_no = gm.grn_no
        LEFT JOIN identity_master im ON gl.isbm = im.isbm
        ORDER BY gl.id
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    df["book_name"] = df["book_name"].fillna("UNKNOWN (add to Identity master)")
    return df


def get_sales_lines_df() -> pd.DataFrame:
    conn = get_conn()
    query = """
        SELECT sl.isbm, im.book_name, sl.qty_sold
        FROM sales_lines sl
        LEFT JOIN identity_master im ON sl.isbm = im.isbm
        ORDER BY sl.id
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    df["book_name"] = df["book_name"].fillna("UNKNOWN (add to Identity master)")
    return df
