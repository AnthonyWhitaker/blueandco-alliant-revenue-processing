"""
Generate a synthetic Medline remittance file plus the Account List and payment
ledger it reconciles against, for demos and testing without real data.

Every name, address, account number and code is made up. The remittance has
the same 22 columns as Medline's real item-level file (one row per invoice
line), and the data deliberately covers the cases the matcher has to handle:

  * GPO ID equal to the member's Premier entity code (exact match)
  * several Medline ship-to accounts for one facility
  * no GPO ID, and a name/address that differs from the Account List (fuzzy)
  * a GPO ID that isn't in the Account List, but a matching name
  * customers that aren't in the Account List at all (end up on Review)
  * credit lines (negative quantities) and a grand-total row at the bottom

Usage:
    .venv/bin/python scripts/make_medline_mock.py [--out samples/medline] [--seed 7]
"""

import argparse
import random
from datetime import date, timedelta
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font

PAYOR = "Medline"
CONTRACT_ID = "MOCK-MED-2026"
PAYMENT_ID = "MOCK-PAY-MED-0926"
RECEIVED = date(2026, 10, 15)
PERIOD_START = date(2026, 9, 1)

HEADERS = [
    "InvoiceNumber", "InvoiceDate", "Medline Account #", "Account Name", "Account Street",
    "Account City", "Account State", "Account Zip", "GPO ID", "Mfr Name", "Mfr Item #",
    "MaterialNumber", "MaterialDescription", "MaterialGroupCode", "InvoiceLineUOMCode",
    "ProductDivisionCode", "SaleToBaseUOMConvFactorQty", "MatUnitPriceAmount",
    "OrigInvcLineUOMInvoiceQty", "Extended Price Amount", "GPO Fee Rate", "GPO Fee Amount",
]

# (account name, street, city, state, zip, premier entity code)
FACILITIES = [
    ("Lakeview Regional Medical Center", "1200 Shoreline Dr", "Austin", "TX", "78701", "TX5012"),
    ("Cedar Ridge Community Hospital", "45 Cedar Ridge Rd", "Round Rock", "TX", "78664", "TX5013"),
    ("Pine Hollow Surgical Center", "880 Pine Hollow Ln", "San Marcos", "TX", "78666", "TX5014"),
    ("Riverbend Children's Hospital", "300 Riverbend Pkwy", "Dallas", "TX", "75201", "TX5021"),
    ("North Fork Health System", "7 North Fork Ave", "Plano", "TX", "75024", "TX5022"),
    ("Mesa Verde Medical Center", "1550 Mesa Blvd", "El Paso", "TX", "79901", "TX5030"),
    ("Bluebonnet Rehabilitation Hospital", "92 Bluebonnet Trl", "Waco", "TX", "76701", "TX5031"),
    ("Harbor Point Hospital", "1 Harbor Point Way", "Corpus Christi", "TX", "78401", "TX5040"),
    ("Sandstone Orthopedic Institute", "410 Sandstone Ct", "Lubbock", "TX", "79401", "TX5041"),
    ("Prairie Wind Medical Center", "2500 Prairie Wind Rd", "Amarillo", "TX", "79101", "TX5050"),
    ("Granite Peak Hospital", "18 Granite Peak Dr", "Denver", "CO", "80202", "CO2101"),
    ("Aspen Grove Medical Center", "640 Aspen Grove Blvd", "Boulder", "CO", "80302", "CO2102"),
    ("Silver Creek Regional Hospital", "3300 Silver Creek Rd", "Colorado Springs", "CO", "80903", "CO2110"),
    ("Red Mesa Community Hospital", "77 Red Mesa St", "Pueblo", "CO", "81003", "CO2111"),
    ("Juniper Valley Health", "905 Juniper Valley Rd", "Grand Junction", "CO", "81501", "CO2120"),
    ("Desert Bloom Medical Center", "1600 Desert Bloom Ave", "Phoenix", "AZ", "85004", "AZ3301"),
    ("Saguaro Heights Hospital", "220 Saguaro Heights Dr", "Tucson", "AZ", "85701", "AZ3302"),
    ("Copper Canyon Surgical Hospital", "58 Copper Canyon Rd", "Mesa", "AZ", "85201", "AZ3310"),
    ("Painted Sky Behavioral Health", "4100 Painted Sky Ln", "Flagstaff", "AZ", "86001", "AZ3311"),
    ("Willow Bend Medical Center", "12 Willow Bend Rd", "Albuquerque", "NM", "87102", "NM4401"),
    ("Turquoise Trail Hospital", "700 Turquoise Trail", "Santa Fe", "NM", "87501", "NM4402"),
    ("High Plains Regional Medical Center", "1450 High Plains Hwy", "Roswell", "NM", "88201", "NM4410"),
    ("Oak Hollow Memorial Hospital", "330 Oak Hollow Dr", "Tulsa", "OK", "74103", "OK5501"),
    ("Redbud Valley Medical Center", "88 Redbud Valley Rd", "Oklahoma City", "OK", "73102", "OK5502"),
    # In the Account List with different names/addresses in the Medline file (fuzzy matches):
    ("Saint Brigid's Hospital", "500 Mission St", "San Antonio", "TX", "78205", "TX5060"),
    ("Mountain View Regional Medical Center", "2100 Summit Ave", "Fort Collins", "CO", "80521", "CO2130"),
    ("Sonoran Valley Health Center", "150 Valley Rd", "Chandler", "AZ", "85225", "AZ3320"),
    ("Great Plains Cancer Institute", "999 Hope Blvd", "Norman", "OK", "73069", "OK5510"),
    ("Rio Grande Community Hospital", "45 River Rd", "Las Cruces", "NM", "88001", "NM4420"),
    # GPO ID in the file is wrong, name matches:
    ("Clearwater Springs Hospital", "61 Clearwater Ln", "Abilene", "TX", "79601", "TX5070"),
    # In the Account List but not in this month's file:
    ("Hillcrest Women's Hospital", "14 Hillcrest Rd", "Tyler", "TX", "75701", "TX5080"),
    ("Evergreen Long Term Care", "250 Evergreen Way", "Longmont", "CO", "80501", "CO2140"),
    ("Mission Hills Surgical Center", "75 Mission Hills Dr", "Yuma", "AZ", "85364", "AZ3330"),
]
# How the fuzzy-match facilities (indexes 24-28) appear in the Medline file.
FUZZY_VARIANTS = {
    24: ("ST BRIGIDS HOSPITAL", "500 MISSION STREET"),
    25: ("MOUNTAIN VIEW REGIONAL MED CTR", "2100 SUMMIT AVENUE"),
    26: ("SONORAN VALLEY HEALTH CTR", "150 VALLEY ROAD"),
    27: ("GREAT PLAINS CANCER INST", "999 HOPE BOULEVARD"),
    28: ("RIO GRANDE COMMUNITY HOSP", "45 RIVER ROAD"),
}
# Extra Medline ship-to accounts for a facility already in the file.
SHIP_TOS = {0: "LAKEVIEW REGIONAL MC - SURGERY DOCK", 3: "RIVERBEND CHILDRENS HOSP - PHARMACY"}
# Customers not in the Account List.
UNKNOWN = [
    ("BRIGHTWATER URGENT CARE", "12 Market St", "Killeen", "TX", "76541"),
    ("SUMMIT FAMILY CLINIC LLC", "8 Peak Rd", "Loveland", "CO", "80537"),
    ("CANYON DIALYSIS PARTNERS", "300 Arroyo Dr", "Scottsdale", "AZ", "85251"),
]

# (Mfr name, description, group code, UOM, division, base units per UOM, unit price)
PRODUCTS = [
    ("Medline", "GLOVE, EXAM, NITRILE, POWDER-FREE, MEDIUM", "GLV01", "CS", "PP", 1000, 62.40),
    ("Medline", "GLOVE, EXAM, NITRILE, POWDER-FREE, LARGE", "GLV01", "CS", "PP", 1000, 62.40),
    ("Medline", "GOWN, ISOLATION, LEVEL 2, UNIVERSAL", "GWN02", "CS", "PP", 100, 48.75),
    ("Medline", "MASK, PROCEDURE, EARLOOP, BLUE", "MSK01", "CS", "PP", 500, 31.20),
    ("Medline", "SYRINGE, LUER LOCK, 10ML", "SYR01", "BX", "NS", 100, 14.85),
    ("Medline", "NEEDLE, HYPODERMIC, 22G X 1IN", "NDL01", "BX", "NS", 100, 9.60),
    ("Medline", "GAUZE SPONGE, 4X4, 12-PLY, STERILE", "WND01", "CS", "WC", 1200, 54.10),
    ("Medline", "DRESSING, FOAM, BORDERED, 4X4", "WND02", "BX", "WC", 10, 38.90),
    ("Medline", "UNDERPAD, DISPOSABLE, 23X36", "INC01", "CS", "PC", 150, 42.30),
    ("Medline", "BRIEF, ADULT, MAXIMUM, LARGE", "INC02", "CS", "PC", 72, 57.80),
    ("Medline", "WIPE, DISINFECTANT, GERMICIDAL", "ENV01", "CS", "EV", 12, 66.00),
    ("Medline", "CATHETER, FOLEY, 16FR, 5ML", "URO01", "CS", "UR", 10, 29.95),
    ("Medline", "TRAY, IV START, WITH CHG", "IVS01", "CS", "NS", 50, 87.50),
    ("Medline", "DRAPE, SURGICAL, UNIVERSAL PACK", "SRG01", "CS", "SP", 8, 112.40),
    ("Medline", "SUCTION CANISTER, 1500ML", "SUC01", "CS", "SP", 48, 71.25),
    ("Cardinal Health", "THERMOMETER PROBE COVER", "DGN01", "CS", "DG", 2000, 44.60),
    ("3M", "TAPE, SURGICAL, PAPER, 1IN", "TAP01", "BX", "WC", 12, 11.70),
    ("BD", "BLOOD COLLECTION TUBE, 4ML, LAVENDER", "LAB01", "BX", "LB", 100, 23.15),
]

FEE_RATE = {"PP": 0.03, "NS": 0.025, "WC": 0.03, "PC": 0.02, "EV": 0.02, "UR": 0.025,
            "SP": 0.035, "DG": 0.02, "LB": 0.015}


def _customers(rng: random.Random) -> list[dict]:
    customers = []
    account_no = iter(rng.sample(range(1_000_000, 9_999_999), 60))
    for i, (name, street, city, state, zip_, pec) in enumerate(FACILITIES[:30]):
        gpo_id = pec
        file_name, file_street = name.upper(), street.upper()
        if i in FUZZY_VARIANTS:
            gpo_id = None
            file_name, file_street = FUZZY_VARIANTS[i]
        if i == 29:
            gpo_id = "TX9999"  # wrong code; the name still matches
        customers.append(dict(acct=next(account_no), name=file_name, street=file_street, city=city.upper(),
                              state=state, zip=zip_, gpo=gpo_id, volume=rng.choice([2, 3, 4, 6, 8])))
        if i in SHIP_TOS:
            customers.append(dict(acct=next(account_no), name=SHIP_TOS[i], street=file_street, city=city.upper(),
                                  state=state, zip=zip_, gpo=pec, volume=2))
    for name, street, city, state, zip_ in UNKNOWN:
        customers.append(dict(acct=next(account_no), name=name, street=street.upper(), city=city.upper(),
                              state=state, zip=zip_, gpo=None, volume=1))
    return customers


def _remittance_rows(rng: random.Random) -> list[list]:
    rows = []
    invoice_no = 4_100_000_000
    for c in _customers(rng):
        for _ in range(c["volume"] * 4):  # invoices per customer this period
            invoice_no += rng.randint(1, 40)
            invoice_date = PERIOD_START + timedelta(days=rng.randint(0, 29))
            for p in rng.sample(range(len(PRODUCTS)), rng.randint(1, 5)):
                mfr, desc, group, uom, division, per_uom, price = PRODUCTS[p]
                qty = rng.randint(1, 12)
                if rng.random() < 0.04:
                    qty = -rng.randint(1, 3)  # credit / return line
                unit_price = round(price * rng.uniform(0.97, 1.03), 2)
                extended = round(unit_price * qty, 2)
                rate = FEE_RATE[division]
                material = f"MDS{192000 + p * 37}"
                rows.append([
                    str(invoice_no), invoice_date, str(c["acct"]), c["name"], c["street"], c["city"],
                    c["state"], c["zip"], c["gpo"], mfr, f"{mfr[:3].upper()}-{material[3:]}",
                    material, desc, group, uom, division, per_uom, unit_price, qty, extended,
                    rate, round(extended * rate, 2),
                ])
    rows.sort(key=lambda r: (r[1], r[0]))
    return rows


def _autosize(ws) -> None:
    for col in ws.columns:
        width = max(len(str(c.value or "")) for c in col[:200])
        ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 45)


def write_remittance(path: Path, rows: list[list]) -> float:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="bottom")
    for r in rows:
        ws.append(r)
    total_ext = round(sum(r[19] for r in rows), 2)
    total_fee = round(sum(r[21] for r in rows), 2)
    ws.append([None] * 18 + ["Grand Total", total_ext, None, total_fee])
    ws.cell(ws.max_row, 19).font = Font(bold=True)
    for row in ws.iter_rows(min_row=2):
        row[1].number_format = "mm/dd/yyyy"
        row[17].number_format = row[19].number_format = row[21].number_format = "#,##0.00"
        row[20].number_format = "0.00%"
    _autosize(ws)
    ws.freeze_panes = "A2"
    wb.save(path)
    return total_fee


def write_account_list(path: Path, rng: random.Random) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Accounts"
    ws.append(["Account ID", "Account Name", "Premier Entity Code", "Alliant Entity Code",
               "Billing Street", "Billing City", "Billing State/Province", "Billing Zip/Postal Code"])
    for i, (name, street, city, state, zip_, pec) in enumerate(FACILITIES):
        ws.append([f"001MOCK{i:08d}", name, pec, f"AL{rng.randint(10000, 99999)}", street, city, state, zip_])
    _autosize(ws)
    wb.save(path)


def write_ledger(path: Path, deposit: float) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ledger"
    ws.append(["Payor", "Payment Received Date", "Deposit Amount", "SF Payment ID", "Contract ID"])
    ws.append([PAYOR, RECEIVED, deposit, PAYMENT_ID, CONTRACT_ID])
    ws.append(["Some Other Supplier", RECEIVED, 1250.00, "MOCK-PAY-OTHER-01", "MOCK-OTH-1"])
    ws["B2"].number_format = ws["B3"].number_format = "mm/dd/yyyy"
    _autosize(ws)
    wb.save(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "samples" / "medline"))
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows = _remittance_rows(rng)
    deposit = round(sum(r[21] for r in rows), 2)
    remittance = out / f"{PAYOR} {RECEIVED:%Y.%m.%d} {deposit:.2f}.xlsx"
    for old in out.glob(f"{PAYOR} *.xlsx"):
        old.unlink()
    write_remittance(remittance, rows)
    write_account_list(out / "Account List (mock).xlsx", rng)
    write_ledger(out / "Ledger (mock).xlsx", deposit)
    print(f"{remittance.name}: {len(rows)} invoice lines, GPO fees {deposit:,.2f}")
    print(f"Wrote it, the Account List and the Ledger to {out}")


if __name__ == "__main__":
    main()
