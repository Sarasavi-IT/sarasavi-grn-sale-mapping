# GRN / Sales Mapping System

A small local web app that replaces the spreadsheet version: upload your raw
GRN and Sales Excel exports, and it automatically fills in Book Name, GRN
Date, and Supplier from master lists, then produces the GRN-wise, ISBM-wise,
and Summary reports using a proper FIFO allocation engine (no formula
row-position bugs).

## 1. Install (one-time)

You need Python 3.10+ installed. Then, in this folder:

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Run

```bash
streamlit run app.py
```

This opens a browser tab at `http://localhost:8501`. Everything runs on your
own machine — no data leaves your computer. The app stores only the current
GRN/Sales upload in `grn_system.db`; importing a new GRN or Sales file replaces
the previous upload for that input type.

Legacy `.xls` files are supported. The supplied `182844_grn.xls` format uses
`Product`, `Description`, and `Qty.`; the GRN number is read from the numeric
prefix in the filename. The supplied `21-08-15-09sales.xls` format is read as
headerless `ISBM`, `Book Name`, and `Sales Qty` columns. Both files may be
uploaded with their headings removed; the complete uploaded data is shown
before import.

## 3. How the tabs work

| Tab | What you do |
|---|---|
| **Upload GRN** | Upload a GRN Excel file. The supplied data-only format uses Product/ISBM, Description/Book Name, Qty., and SIH. |
| **Upload Sales** | Upload a sales Excel file. The supplied data-only format uses ISBM, Book Name, and Sales Qty. |
| **GRN Mapping Report** | GRN stock mapped to sales using FIFO, including SIH quantities. Downloadable as Excel. |
| **Sales Mapping Report** | Each sales row mapped to available GRN stock, including unmapped quantities. Downloadable as Excel. |
| **Summary** | GRN, SIH, available stock, sales, mapped sales, and unmapped sales totals. Export the complete five-sheet workbook. |

## 4. Migrating your existing spreadsheet data (one-time)

If you have the old `GRN_Sales_Mapping_System.xlsx`, you can seed this system
from it:

1. Open the old file's `GRN_INPUT` sheet, save/export it as its own `.xlsx`,
   and upload it on the **Upload GRN** tab.
2. Do the same for `SALES_INPUT` → **Upload Sales** tab.

Since the old file's columns are already named `GRN No`, `GRN Date`,
`Supplier`, `ISBM`, `Book Name`, `GRN Qty` / `Sales Qty`, they'll be recognized
automatically.

## 5. Notes on the FIFO logic

- Within each ISBM, GRN batches are consumed oldest-date-first.
- A GRN batch is `FULL` once fully sold through, `PARTIAL` if some of it has
  sold, `UNSOLD` if none has.
- If an ISBM has sales but no matching GRN batch at all, it shows up in the
  ISBM-wise report as `UNMAPPED SALES (no GRN on file)` — this usually means
  a GRN entry is still missing, not a data error.

## 6. Extending this later

This is intentionally a single-file SQLite setup so it's easy to run
anywhere. If you outgrow it (multiple people entering data at once, needing
logins, etc.), the `database.py` module can be pointed at PostgreSQL instead
with only small changes, and the same `mapping_engine.py` logic carries over
unchanged.
