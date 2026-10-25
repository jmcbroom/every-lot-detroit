"""Build the auction parcel list (auction_parcels.csv) from the Wayne County
Treasurer's "properties sold" PDF.

The PDF is an Excel export: one row per sold parcel, with fixed column headers
repeated on every page. Words are assigned to columns by their x-position
relative to that page's header, which survives the uneven spacing that breaks
plain-text parsing. Only Detroit parcels are kept, and only the fields the bot
posts (the bidder's street address and bidder number are dropped).

Requires pdfplumber, which the bot itself doesn't need:

    pip install pdfplumber
    python build_auction_list.py properties-sold-in-the-2026-september-auction.pdf
"""
import argparse
import csv
import re
import sys

import pdfplumber

COLUMNS = [
    "PARCEL_ID",
    "PROP_ADDRESS",
    "PROP_CITY_ZIP",
    "SALE_AMT",
    "BIDDER_#",
    "BIDDER_NAME",
    "BIDDER_ADDRESS",
    "BIDDER_CITY_STATE_ZIP",
]

# Right-aligned numbers (SALE_AMT) start left of their header's x0 only when
# very wide; this slack lets a word start slightly before its header.
X_TOLERANCE = 3

# "NOVI MI 48377" / "VAN BUREN TWP MI 48111" / "TORONTO ON M5V 2T6"
CITY_STATE_ZIP_RE = re.compile(r"^(?P<city>.+?)\s+(?P<state>[A-Z]{2})\s+(?P<zip>[0-9A-Z -]+)$")

OUTPUT_FIELDS = ["parcel_id", "address", "sale_amount", "bidder_name", "bidder_city", "bidder_state"]


def page_rows(page):
    """Yield {column: text} dicts for each data row on a page."""
    words = page.extract_words()
    header = {w["text"]: w["x0"] for w in words if w["text"] in COLUMNS}
    if len(header) != len(COLUMNS):
        return
    starts = sorted((x0, name) for name, x0 in header.items())
    header_top = min(w["top"] for w in words if w["text"] in COLUMNS)

    lines = {}
    for w in words:
        if w["top"] <= header_top + 1:
            continue
        lines.setdefault(round(w["top"]), []).append(w)

    for top in sorted(lines):
        cells = {name: [] for name in COLUMNS}
        for w in sorted(lines[top], key=lambda w: w["x0"]):
            column = starts[0][1]
            for x0, name in starts:
                if w["x0"] + X_TOLERANCE >= x0:
                    column = name
            cells[column].append(w["text"])
        row = {name: " ".join(parts) for name, parts in cells.items()}
        # Skip page titles/footers ("1 of 51"), which have no parcel/bidder.
        if row["PARCEL_ID"] and re.fullmatch(r"\d{8}", row["BIDDER_#"]):
            yield row


def to_record(row):
    """Convert a raw row to an output record, or None (with a reason) if it
    doesn't parse cleanly."""
    if not row["PROP_CITY_ZIP"].startswith("DETROIT "):
        return None, None
    try:
        amount = float(row["SALE_AMT"].replace(",", ""))
    except ValueError:
        return None, f"bad sale amount {row['SALE_AMT']!r}"
    match = CITY_STATE_ZIP_RE.match(row["BIDDER_CITY_STATE_ZIP"])
    if not match or not row["BIDDER_NAME"]:
        return None, f"bad bidder fields {row['BIDDER_NAME']!r} / {row['BIDDER_CITY_STATE_ZIP']!r}"
    return {
        "parcel_id": row["PARCEL_ID"],
        "address": row["PROP_ADDRESS"],
        "sale_amount": f"{amount:.2f}",
        "bidder_name": row["BIDDER_NAME"],
        "bidder_city": match["city"],
        "bidder_state": match["state"],
    }, None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdf", help="Path to the county's properties-sold PDF")
    parser.add_argument("--output", default="auction_parcels.csv")
    args = parser.parse_args()

    records = []
    problems = []
    with pdfplumber.open(args.pdf) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            for row in page_rows(page):
                record, problem = to_record(row)
                if record:
                    records.append(record)
                elif problem:
                    problems.append(f"page {page_number}, {row['PARCEL_ID']}: {problem}")

    with open(args.output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(records)

    print(f"Wrote {len(records)} Detroit parcels to {args.output}")
    for problem in problems:
        print(f"Skipped {problem}", file=sys.stderr)


if __name__ == "__main__":
    main()
