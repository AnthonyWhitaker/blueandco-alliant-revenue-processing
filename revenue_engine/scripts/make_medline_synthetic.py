"""
Generate a synthetic member Account List and a messy synthetic Medline file,
with a label per file row saying which member it should match and why it's
messy, for scoring the member matcher without real data.

Every name, address, account number and code is made up (cities and ZIP
prefixes are real so ZIP/city/state logic behaves normally).

Outputs (in --out, default samples/medline):
  synthetic_account_list.xlsx      ~1,000 members in the Account List layout load_account_list() reads,
                                   plus Billing Street / Site Role / Is Primary columns it ignores. Facilities
                                   with several ship-to sites share one Premier Entity Code; one is primary.
  synthetic_incoming.csv           ~500 invoice lines with Medline's 22 columns, plus a grand-total row.
  synthetic_incoming_labels.csv    one row per invoice line (joined on source_row = spreadsheet row):
                                   scenario, messiness tags, GPO ID state, expected member.

The incoming file also reuses the 32 accounts of "GPO Key (mock).csv" (their
members are the mock members 1-33 already in data/revenue.db) so the GPO-key
verifier has something to verify.

    .venv/bin/python scripts/make_medline_synthetic.py [--out samples/medline] [--seed 11] [--rows 500]
"""

import argparse
import csv
import random
import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

HEADERS = [
    "InvoiceNumber", "InvoiceDate", "Medline Account #", "Account Name", "Account Street",
    "Account City", "Account State", "Account Zip", "GPO ID", "Mfr Name", "Mfr Item #",
    "MaterialNumber", "MaterialDescription", "MaterialGroupCode", "InvoiceLineUOMCode",
    "ProductDivisionCode", "SaleToBaseUOMConvFactorQty", "MatUnitPriceAmount",
    "OrigInvcLineUOMInvoiceQty", "Extended Price Amount", "GPO Fee Rate", "GPO Fee Amount",
]

# state -> [(city, ZIP prefix)]
GEO = {
    "TX": [("Austin", "787"), ("Dallas", "752"), ("Houston", "770"), ("San Antonio", "782"), ("Fort Worth", "761")],
    "CO": [("Denver", "802"), ("Colorado Springs", "809"), ("Fort Collins", "805"), ("Pueblo", "810")],
    "AZ": [("Phoenix", "850"), ("Tucson", "857"), ("Mesa", "852")],
    "NM": [("Albuquerque", "871"), ("Santa Fe", "875"), ("Las Cruces", "880")],
    "OK": [("Tulsa", "741"), ("Oklahoma City", "731")],
    "MA": [("Boston", "021"), ("Worcester", "016"), ("Springfield", "011")],
    "CT": [("Hartford", "061"), ("New Haven", "065")],
    "NJ": [("Newark", "071"), ("Trenton", "086"), ("Camden", "081")],
    "VA": [("Richmond", "232"), ("Norfolk", "235"), ("Roanoke", "240")],
    "GA": [("Atlanta", "303"), ("Savannah", "314")],
    "OH": [("Columbus", "432"), ("Cleveland", "441"), ("Dayton", "454")],
    "PA": [("Pittsburgh", "152"), ("Harrisburg", "171")],
    "FL": [("Tampa", "336"), ("Orlando", "328"), ("Saint Petersburg", "337"), ("Fort Myers", "339")],
}
STATE_NAMES = {"TX": "Texas", "CO": "Colorado", "AZ": "Arizona", "NM": "New Mexico", "OK": "Oklahoma",
               "MA": "Massachusetts", "CT": "Connecticut", "NJ": "New Jersey", "VA": "Virginia",
               "GA": "Georgia", "OH": "Ohio", "PA": "Pennsylvania", "FL": "Florida"}
CITY_VARIANTS = {"Fort Worth": "Ft Worth", "Fort Collins": "Ft. Collins", "Fort Myers": "Ft Myers",
                 "Saint Petersburg": "St Petersburg", "Colorado Springs": "Colorado Spgs",
                 "Pittsburgh": "Pittsburg", "Oklahoma City": "Oklahoma Cty", "Albuquerque": "Albuquerqe"}
LEADING_ZERO_STATES = ["MA", "CT", "NJ"]

PLACE_A = ["Cedar", "Maple", "Willow", "Stone", "River", "Lake", "Pine", "Oak", "Elm", "Silver", "Golden",
           "Clear", "Green", "Red", "Blue", "High", "Fair", "Spring", "Bright", "Glen", "Sun", "Harbor",
           "Prairie", "Canyon", "Valley", "Summit", "Amber", "Hazel", "Copper", "Ivy"]
PLACE_B = ["Ridge", "Creek", "Brook", "Falls", "Grove", "Bend", "Point", "View", "Crest", "Field", "Hollow",
           "Springs", "Haven", "Park", "Meadow", "Bluff", "Heights", "Wood", "Vale", "Crossing"]
FIRST = ["John", "Mary", "William", "Elizabeth", "James", "Margaret", "Thomas", "Helen", "George", "Anne",
         "Charles", "Ruth"]
SURNAMES = ["Pemberton", "Ashford", "Calloway", "Danforth", "Ellery", "Fairbanks", "Garrison", "Holloway",
            "Langford", "Merriweather", "Norcross", "Prescott", "Radford", "Stratton", "Thornbury", "Wexford",
            "Yardley", "Brannock", "Corwin", "Everly", "Greaves", "Hartwell", "Ingram", "Lockhart", "Marlowe",
            "Pendry", "Quill", "Rowntree", "Sedgwick", "Tolliver"]
SAINTS = ["Agnes", "Bernard", "Catherine", "Dominic", "Elias", "Francis", "Gabriel", "Hilda", "Ignatius",
          "Jude", "Lucia", "Martin", "Nicholas", "Rita", "Vincent", "Clare", "Anselm", "Bridget"]
MOUNTS = ["Carmel", "Hermon", "Olive", "Pleasant", "Zion", "Laurel", "Vernon", "Hope"]
TYPES = ["Hospital", "Medical Center", "Regional Medical Center", "Community Hospital", "Health Center",
         "Surgery Center", "Rehabilitation Hospital", "Behavioral Health", "Cancer Institute", "Health System",
         "Clinic", "Nursing and Rehabilitation Center"]
STREET_NAMES = ["Shoreline", "Main", "Oak", "Hospital", "Medical Park", "Lincoln", "Washington", "Park",
                "Highland", "Lakeview", "Center", "College", "Mill", "Church", "Market", "Sunset", "Ridgeway",
                "Commerce", "Madison", "Jefferson", "Franklin", "Hillcrest", "Orchard", "Meadowbrook"]
STREET_SUFFIX = {"Street": "St", "Avenue": "Ave", "Road": "Rd", "Drive": "Dr", "Boulevard": "Blvd",
                 "Lane": "Ln", "Parkway": "Pkwy", "Highway": "Hwy", "Court": "Ct", "Way": "Way"}
DIRECTIONS = {"North": "N", "South": "S", "East": "E", "West": "W"}
# (full word or phrase, abbreviations) applied whole-word to names
NAME_ABBREV = [
    ("Regional Medical Center", ["RMC", "Reg Med Ctr", "Regional Med Ctr"]),
    ("Medical Center", ["Med Ctr", "MC", "Med Center"]),
    ("Community Hospital", ["Comm Hosp", "Community Hosp"]),
    ("Memorial Hospital", ["Mem Hosp", "Memorial Hosp"]),
    ("Health Center", ["Hlth Ctr", "Health Ctr"]),
    ("Surgery Center", ["Surg Ctr", "ASC"]),
    ("Rehabilitation Hospital", ["Rehab Hosp"]),
    ("Behavioral Health", ["Behav Hlth", "BH"]),
    ("Cancer Institute", ["Cancer Inst"]),
    ("Health System", ["Hlth Sys", "Health Sys"]),
    ("Nursing and Rehabilitation Center", ["Nursing & Rehab Ctr", "Nsg & Rehab"]),
    ("Home Health", ["HH"]),
    ("University", ["Univ"]),
    ("Children's", ["Childrens", "Child"]),
    ("Hospital", ["Hosp"]),
    ("Saint", ["St", "St."]),
    ("Mount", ["Mt", "Mt."]),
    ("Location", ["Loc"]),
    ("Outpatient", ["Outpt", "OP"]),
    ("Center", ["Ctr"]),
]
SHIP_TO_ROLES = ["Home Health - Location {n}", "Surgery Center", "Outpatient Imaging", "Pharmacy",
                 "Hospice", "Physical Therapy", "Receiving Dock"]
DECOY_TYPES = ["Veterinary Clinic", "Dental Group", "Family Pharmacy", "Chiropractic", "Fitness Center",
               "Animal Hospital", "Eye Care"]
SHIP_QUALIFIERS = [" - DOCK", " RECEIVING", " - MATERIALS MGMT", " WAREHOUSE", " ATTN STOREROOM"]

PRODUCTS = [
    ("GLOVE,EXAM,NITRILE,PF,LG", "Medline", "CS", 10), ("GAUZE,SPONGE,4X4,12PLY,STRL", "Medline", "CS", 20),
    ("SYRINGE,10ML,LUER LOCK", "Northfield Medical", "BX", 100), ("UNDERPAD,DISPOSABLE,23X36", "Medline", "CS", 15),
    ("WIPE,DISINFECTANT,LG", "Cleanpath Labs", "CS", 12), ("CATHETER,FOLEY,16FR,2-WAY", "Vantage Urology", "BX", 10),
    ("MASK,PROCEDURE,EARLOOP", "Medline", "BX", 50), ("DRESSING,FOAM,BORDER,4X4", "Harbor Wound Care", "BX", 10),
    ("GOWN,ISOLATION,YELLOW,XL", "Medline", "CS", 50), ("TUBE,BLOOD COLLECTION,4ML", "Crestline Diagnostics", "CS", 1000),
]


class Gen:
    def __init__(self, seed: int):
        self.r = random.Random(seed)
        self.used_names: set[str] = set()
        self.used_codes: set[str] = set()
        self.used_accounts: set[str] = set()
        self.seq = 0

    # -- identifiers ---------------------------------------------------------
    def account_id(self) -> str:
        self.seq += 1
        return f"001SYN{self.seq:09d}"

    def pec(self, st: str) -> str:
        while True:
            code = f"{st}{self.r.randint(100000, 999999)}"
            if code not in self.used_codes:
                self.used_codes.add(code)
                return code

    def alliant(self) -> str:
        return f"AL{self.r.randint(100000, 999999)}"

    def medline_account(self, taken: set[str]) -> str:
        while True:
            n = str(self.r.randint(4000000, 4999999))   # mock/JDE-history accounts are elsewhere
            if n not in self.used_accounts and n not in taken:
                self.used_accounts.add(n)
                return n

    # -- names and places ----------------------------------------------------
    def place(self) -> str:
        a, b = self.r.choice(PLACE_A), self.r.choice(PLACE_B)
        return f"{a}{b.lower()}" if self.r.random() < 0.15 else f"{a} {b}"

    def facility_name(self) -> str:
        for _ in range(200):
            k = self.r.random()
            if k < 0.45:
                name = f"{self.place()} {self.r.choice(TYPES)}"
            elif k < 0.57:
                name = f"{self.r.choice(FIRST)} {self.r.choice(SURNAMES)} Memorial Hospital"
            elif k < 0.67:
                name = f"{self.r.choice(SURNAMES)} {self.r.choice(TYPES)}"
            elif k < 0.79:
                s = self.r.choice(SAINTS)
                name = f"Saint {s}'s Hospital" if self.r.random() < 0.4 else f"Saint {s} {self.r.choice(TYPES)}"
            elif k < 0.85:
                name = f"Mount {self.r.choice(MOUNTS)} {self.r.choice(TYPES)}"
            elif k < 0.90:
                name = f"{self.place()} University Medical Center"
            elif k < 0.95:
                name = f"{self.place()} Children's Hospital"
            else:
                name = f"{self.place()} Health System"
            if name.upper() not in self.used_names:
                self.used_names.add(name.upper())
                return name
        raise RuntimeError("ran out of names")

    def city_zip(self, st: str, city: str = None) -> tuple[str, str]:
        city_, prefix = self.r.choice(GEO[st]) if city is None else next(c for c in GEO[st] if c[0] == city)
        return city_, f"{prefix}{self.r.randint(1, 40):02d}"   # few ZIPs per city: shared ZIP buckets

    def street(self) -> str:
        num = self.r.randint(1, 9800)
        d = f"{self.r.choice(list(DIRECTIONS))} " if self.r.random() < 0.2 else ""
        s = f"{num} {d}{self.r.choice(STREET_NAMES)} {self.r.choice(list(STREET_SUFFIX))}"
        if self.r.random() < 0.25:
            s += f" Suite {self.r.randint(1, 9) * 100 + self.r.randint(0, 20)}"
        return s

    # -- messiness -----------------------------------------------------------
    def abbreviate(self, name: str) -> str:
        out = name
        hits = [(full, abbrs) for full, abbrs in NAME_ABBREV if re.search(rf"\b{re.escape(full)}\b", out)]
        if not hits:
            return out
        self.r.shuffle(hits)
        for i, (full, abbrs) in enumerate(hits):
            if i == 0 or self.r.random() < 0.5:
                out = re.sub(rf"\b{re.escape(full)}\b", self.r.choice(abbrs), out, count=1)
        return out

    def informal(self, name: str) -> str:
        """Shortened/informal: what someone at Medline might have typed."""
        options = []
        m = re.match(r"(\w+) (\w+) Memorial Hospital$", name)
        if m:
            options += [f"{m[1]} {m[2]} hospital", f"{m[2]} Memorial", f"{m[2]} Hospital"]
        core = re.sub(r"\b(Regional|Community|University|Memorial)\b ?", "", name).strip()
        bare = re.sub(r"\b(Medical Center|Hospital|Health Center|Health System|Clinic|Surgery Center|"
                      r"Rehabilitation Hospital|Behavioral Health|Cancer Institute|"
                      r"Nursing and Rehabilitation Center|Center)\b", "", name)
        bare = re.sub(r"\b(Regional|Community|University|Memorial)\b", "", bare)
        bare = " ".join(bare.split())
        if core != name:
            options.append(core)
        if bare and bare != name:
            options += [bare, f"{bare} hospital", f"{bare} Medical"]
        if name.startswith("Saint "):
            options.append(re.sub(r"^Saint (\w+)('s)?", r"St \1s", name))
        if not options:
            options.append(name.rsplit(" ", 1)[0])
        return self.r.choice(options)

    def punct(self, name: str) -> str:
        ops = [
            lambda s: s.replace(" ", "-", 1) if " " in s else s + ".",
            lambda s: s + self.r.choice([", Inc.", " LLC", ", L.L.C.", " Inc"]),
            lambda s: s.replace(" ", "  ", 1),
            lambda s: s.replace(" ", "", 1),
            lambda s: s.replace(" and ", " & ") if " and " in s else s.replace("'", ""),
            lambda s: s.replace("'s", "s") if "'s" in s else s + ".",
            lambda s: s.replace("Saint ", "St. ") if "Saint " in s else s.replace(" ", " - ", 1),
        ]
        out = name
        for op in self.r.sample(ops, self.r.choice([1, 1, 2])):
            out = op(out)
        return out

    def street_variant(self, street: str) -> str:
        s = street
        for full, ab in STREET_SUFFIX.items():
            s = re.sub(rf"\b{full}\b", ab + ("." if self.r.random() < 0.25 else ""), s)
        for full, ab in DIRECTIONS.items():
            s = re.sub(rf"\b{full}\b", ab, s)
        s = re.sub(r"\bSuite\b", self.r.choice(["Ste", "STE", "#", "Ste."]), s)
        s = s.replace("# ", "#")
        return s

    def street_expand(self, street: str) -> str:
        s = street.upper()
        for full, ab in STREET_SUFFIX.items():
            s = re.sub(rf"\b{ab.upper()}\b\.?", full.upper(), s)
        return s

    def zip_variant(self, z: str, kind: str) -> str:
        if kind == "zip_plus4":
            return f"{z}-{self.r.randint(1000, 9999)}"
        if kind == "zip_9digit_nodash":
            return f"{z}{self.r.randint(1000, 9999)}"
        if kind == "zip_lost_leading_zero":
            return z.lstrip("0")
        if kind == "zip_typo":
            i = self.r.randint(3, 4)
            d = str((int(z[i]) + self.r.choice([1, 9])) % 10)
            return z[:i] + d + z[i + 1:]
        if kind == "zip_blank":
            return ""
        return z


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

def build_members(g: Gen, n_single: int, n_groups: int) -> list[dict]:
    members = []
    states = list(GEO)

    def member(name, st, city, z, street, pec, role, primary, group):
        m = dict(account_id=g.account_id(), name=name, pec=pec, alliant=g.alliant(), city=city, state=st,
                 zip=z, street=street, role=role, primary=primary, group=group)
        members.append(m)
        return m

    for i in range(n_single):
        st = g.r.choice(states)
        city, z = g.city_zip(st)
        pec = g.pec(st) if g.r.random() > 0.04 else ""     # a few members have only an Alliant code
        member(g.facility_name(), st, city, z, g.street(), pec, "single site", "Y", f"S{i}")

    for i in range(n_groups):
        st = g.r.choice(states)
        city, z = g.city_zip(st)
        name = g.facility_name()
        pec = g.pec(st)
        primary = member(name, st, city, z, g.street(), pec, "primary", "Y", f"G{i}")
        base = re.sub(r"\b(Medical Center|Hospital|Health System|Health Center|Regional Medical Center)\b$",
                      "", name).strip() or name
        roles = g.r.sample(SHIP_TO_ROLES, g.r.randint(1, 4))
        loc = 0
        for role in roles:
            if "{n}" in role:
                loc += 1
                role = role.format(n=loc)
            # same city, other street; half the time another ZIP, sometimes another city in the state
            if g.r.random() < 0.2 and len(GEO[st]) > 1:
                c2, z2 = g.city_zip(st)
            elif g.r.random() < 0.5:
                c2, z2 = g.city_zip(st, city)
            else:
                c2, z2 = city, z
            sname = f"{base} {role}" if "Location" in role or role in ("Surgery Center", "Outpatient Imaging",
                                                                        "Hospice", "Physical Therapy") \
                else f"{name} - {role}"
            g.used_names.add(sname.upper())
            member(sname, st, c2, z2, g.street(), pec, role, "N", f"G{i}")
        primary["ship_to_count"] = len(roles)
    return members


# ---------------------------------------------------------------------------
# Incoming accounts
# ---------------------------------------------------------------------------

SCENARIOS = [  # (scenario, synthetic accounts)
    ("clean", 15), ("name_short_informal", 25), ("name_abbreviation", 30), ("name_punct_spacing", 20),
    ("name_mixed", 15), ("ship_to_on_file", 20), ("ship_to_not_on_file", 15), ("location_variant", 15),
    ("no_valid_match_decoy", 10), ("no_valid_match_unrelated", 10),
]


def gpo_state(g: Gen, scenario: str, has_pec: bool) -> str:
    x = g.r.random()
    if scenario.startswith("no_valid_match"):
        return "blank" if x < 0.7 else ("unknown_code" if x < 0.9 else "wrong_member_code")
    if not has_pec:
        return "blank" if x < 0.75 else "unknown_code"
    return "correct" if x < 0.40 else "blank" if x < 0.80 else "wrong_member_code" if x < 0.90 else "unknown_code"


def build_accounts(g: Gen, members: list[dict], mock_key: list[dict], mock_members: dict) -> list[dict]:
    accounts = []
    taken = {k["Medline Account Number"] for k in mock_key}
    singles = [m for m in members if m["role"] in ("single site", "primary")]
    ship_tos = [m for m in members if m["primary"] == "N"]
    primaries = [m for m in members if m["role"] == "primary"]
    pecs = [m["pec"] for m in members if m["pec"]]
    used_targets: set[str] = set()

    def pick(pool, pred=lambda m: True):
        cands = [m for m in pool if m["account_id"] not in used_targets and pred(m)]
        m = g.r.choice(cands)
        used_targets.add(m["account_id"])
        return m

    def account(scenario, tags, target, name, street, city, st, z, expected_entity=None):
        has_pec = bool(target and target["pec"])
        gs = gpo_state(g, scenario, has_pec)
        exp_code = expected_entity if expected_entity is not None else (target["pec"] or target["alliant"]) \
            if target else ""
        gpo = {"correct": (target or {}).get("pec", ""), "blank": "",
               "wrong_member_code": g.r.choice([p for p in pecs if p != exp_code]),
               "unknown_code": f"{st}{g.r.randint(100000, 999999)}X"[:8]}[gs]
        # secondary messiness, applied across scenarios
        if street and g.r.random() < 0.6:
            street = g.street_variant(street)
            tags.append("street_suffix_variant")
        zk = g.r.random()
        if "zip_" not in " ".join(tags):
            if zk < 0.20:
                z = g.zip_variant(z, "zip_plus4"); tags.append("zip_plus4")
            elif zk < 0.25:
                z = g.zip_variant(z, "zip_9digit_nodash"); tags.append("zip_9digit_nodash")
        if g.r.random() < 0.75:
            name, street, city = name.upper(), street.upper(), city.upper()
        accounts.append(dict(
            account=g.medline_account(taken), scenario=scenario, tags=tags or ["clean"], gpo_state=gs,
            name=name, street=street, city=city, state=st, zip=z, gpo_id=gpo,
            expected=target, expected_entity=exp_code, prior_jde_history="N",
        ))

    for scenario, n in SCENARIOS:
        for _ in range(n):
            if scenario == "clean":
                t = pick(singles)
                account(scenario, [], t, t["name"], t["street"], t["city"], t["state"], t["zip"])
            elif scenario == "name_short_informal":
                t = pick(singles)
                account(scenario, ["name_short_informal"], t, g.informal(t["name"]), t["street"], t["city"],
                        t["state"], t["zip"])
            elif scenario == "name_abbreviation":
                t = pick(singles, lambda m: g.abbreviate(m["name"]) != m["name"])
                account(scenario, ["name_abbreviation"], t, g.abbreviate(t["name"]), t["street"], t["city"],
                        t["state"], t["zip"])
            elif scenario == "name_punct_spacing":
                t = pick(singles)
                account(scenario, ["name_punct_spacing"], t, g.punct(t["name"]), t["street"], t["city"],
                        t["state"], t["zip"])
            elif scenario == "name_mixed":
                t = pick(singles)
                nm = g.punct(g.abbreviate(g.informal(t["name"]) if g.r.random() < 0.5 else t["name"]))
                account(scenario, ["name_abbreviation", "name_punct_spacing"], t, nm, t["street"], t["city"],
                        t["state"], t["zip"])
            elif scenario == "ship_to_on_file":
                t = pick(ship_tos)
                nm = g.abbreviate(t["name"]) if g.r.random() < 0.6 else t["name"]
                nm = re.sub(r"(.*) (Home Health|HH) - (Location|Loc) (\d)",
                            lambda m: f"{m[1]} {m[3]} {m[4]} {m[2]}", nm)   # "Cedar Ridge Location 1 HH"
                tags = ["ship_to_on_file"] + (["name_abbreviation"] if nm != t["name"] else [])
                account(scenario, tags, t, nm, t["street"], t["city"], t["state"], t["zip"])
            elif scenario == "ship_to_not_on_file":
                t = pick(primaries)
                city, z = g.city_zip(t["state"])
                while z == t["zip"]:
                    city, z = g.city_zip(t["state"])
                nm = (g.abbreviate(t["name"]) if g.r.random() < 0.5 else t["name"]) + g.r.choice(SHIP_QUALIFIERS)
                account(scenario, ["ship_to_not_on_file"], t, nm, g.street(), city, t["state"], z)
            elif scenario == "location_variant":
                kind = g.r.choice(["zip_lost_leading_zero", "zip_typo", "city_variant", "state_full_name",
                                   "zip_blank"])
                if kind == "zip_lost_leading_zero":
                    t = pick(singles, lambda m: m["state"] in LEADING_ZERO_STATES)
                elif kind == "city_variant":
                    t = pick(singles, lambda m: m["city"] in CITY_VARIANTS)
                else:
                    t = pick(singles)
                city, st, z = t["city"], t["state"], t["zip"]
                if kind == "city_variant":
                    city = CITY_VARIANTS[city]
                elif kind == "state_full_name":
                    st = STATE_NAMES[st]
                else:
                    z = g.zip_variant(z, kind)
                nm = g.abbreviate(t["name"]) if g.r.random() < 0.4 else t["name"]
                tags = [kind] + (["name_abbreviation"] if nm != t["name"] else [])
                account(scenario, tags, t, nm, t["street"], city, st, z)
            elif scenario == "no_valid_match_decoy":
                near = pick(singles)
                base = re.sub(r"\b(Regional |Community |University )?(Medical Center|Hospital|Health Center|"
                              r"Health System|Clinic|Surgery Center|Behavioral Health)\b.*$", "", near["name"]).strip()
                nm = f"{base or near['name']} {g.r.choice(DECOY_TYPES)}"
                account(scenario, ["no_valid_match_decoy"], None, nm, g.street(), near["city"], near["state"],
                        near["zip"])
            elif scenario == "no_valid_match_unrelated":
                st = g.r.choice(list(GEO))
                city, z = g.city_zip(st)
                account(scenario, ["no_valid_match_unrelated"], None, g.facility_name(), g.street(), city, st, z)

    # The mock GPO key's accounts: same account numbers (and JDE history) as the mock data.
    for k in mock_key:
        code = k["Final GPO ID"]
        t = mock_members[code]
        tags = ["mock_key_account"]
        name, street = k["Medline Account Name"], k["Medline Street"]
        x = g.r.random()
        if x < 0.2:
            street = g.street_expand(street); tags.append("street_suffix_variant")
        elif x < 0.35:
            name = g.punct(name.title()).upper(); tags.append("name_punct_spacing")
        elif x < 0.45:
            name = g.informal(name.title()).upper(); tags.append("name_short_informal")
        gx = g.r.random()
        gs = "correct" if gx < 0.5 else "blank" if gx < 0.8 else "wrong_member_code"
        gpo = {"correct": code, "blank": "", "wrong_member_code": g.r.choice([c for c in mock_members if c != code])}[gs]
        accounts.append(dict(
            account=k["Medline Account Number"], scenario="mock_key_account", tags=tags, gpo_state=gs,
            name=name, street=street, city=t["city"].upper(), state=t["state"], zip=t["zip"], gpo_id=gpo,
            expected=t, expected_entity=code, prior_jde_history="Y",
        ))
    return accounts


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def write_account_list(members: list[dict], path: Path) -> None:
    pd.DataFrame([{
        "Account ID": m["account_id"], "Account Name": m["name"], "Premier Entity Code": m["pec"],
        "Alliant Entity Code": m["alliant"], "Billing Street": m["street"], "Billing City": m["city"],
        "Billing State/Province": m["state"], "Billing Zip/Postal Code": m["zip"],
        "Site Role": m["role"], "Is Primary": m["primary"], "Site Group": m["group"],
    } for m in members]).to_excel(path, sheet_name="Account List", index=False)


def write_incoming(g: Gen, accounts: list[dict], rows: int, out: Path) -> None:
    counts = [g.r.randint(1, 4) for _ in accounts]
    while sum(counts) < rows:
        counts[g.r.randrange(len(counts))] += 1
    while sum(counts) > rows:
        i = g.r.randrange(len(counts))
        if counts[i] > 1:
            counts[i] -= 1
    lines = []
    for a, n in zip(accounts, counts):
        lines += [a] * n
    g.r.shuffle(lines)

    data_rows, labels = [], []
    inv_base = 81000000
    total_ext = total_fee = 0.0
    for i, a in enumerate(lines):
        desc, mfr, uom, conv = g.r.choice(PRODUCTS)
        qty = g.r.randint(1, 30) * (-1 if g.r.random() < 0.03 else 1)
        price = round(g.r.uniform(2, 180), 2)
        ext = round(price * qty, 2)
        fee = round(ext * 0.0445, 2)
        total_ext += ext
        total_fee += fee
        data_rows.append([
            str(inv_base + i // 3), (date(2026, 9, 1) + timedelta(days=g.r.randint(0, 29))).strftime("%m/%d/%Y"),
            a["account"], a["name"], a["street"], a["city"], a["state"], a["zip"], a["gpo_id"], mfr,
            f"{mfr[:3].upper()}{g.r.randint(10000, 99999)}", f"MDS{g.r.randint(100000, 999999)}", desc,
            f"G{g.r.randint(100, 999)}", uom, str(g.r.choice([82, 41, 17, 63])), str(conv), f"{price:.2f}",
            str(qty), f"{ext:.2f}", "4.45%", f"{fee:.2f}",
        ])
        t = a["expected"] or {}
        labels.append({
            "source_row": i + 2, "invoice_number": data_rows[-1][0], "medline_account": a["account"],
            "scenario": a["scenario"], "messiness": ";".join(a["tags"]), "gpo_id_state": a["gpo_state"],
            "prior_jde_history": a["prior_jde_history"],
            "expected_account_id": t.get("account_id", ""), "expected_entity_code": a["expected_entity"],
            "expected_site_role": t.get("role", ""), "expected_is_primary": t.get("primary", ""),
            "expected_name": t.get("name", ""), "expected_city": t.get("city", ""),
            "expected_state": t.get("state", ""), "expected_zip": t.get("zip", ""),
        })
    total = [""] * len(HEADERS)
    total[HEADERS.index("Extended Price Amount")] = f"{total_ext:.2f}"
    total[HEADERS.index("GPO Fee Amount")] = f"{total_fee:.2f}"

    with open(out / "synthetic_incoming.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADERS)
        w.writerows(data_rows)
        w.writerow(total)
    pd.DataFrame(labels).to_csv(out / "synthetic_incoming_labels.csv", index=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=str(Path(__file__).parent.parent / "samples" / "medline"))
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--rows", type=int, default=500)
    parser.add_argument("--singles", type=int, default=600)
    parser.add_argument("--groups", type=int, default=120)
    args = parser.parse_args()
    out = Path(args.out)

    with open(out / "GPO Key (mock).csv", newline="", encoding="utf-8") as f:
        mock_key = list(csv.DictReader(f))
    # The mock members the key's GPO IDs belong to (as in scripts/make_medline_mock.py / data/revenue.db)
    mock_df = pd.read_excel(out / "Account List (mock).xlsx", dtype=str).fillna("")
    mock_members = {}
    for r in mock_df.to_dict("records"):
        code = r.get("Premier Entity Code", "")
        if code:
            mock_members[code] = dict(
                account_id=r.get("Account ID", ""), name=r["Account Name"], pec=code,
                alliant=r.get("Alliant Entity Code", ""), city=r.get("Billing City", ""),
                state=r.get("Billing State/Province", ""), zip=r.get("Billing Zip/Postal Code", ""),
                role="single site", primary="Y")

    g = Gen(args.seed)
    members = build_members(g, args.singles, args.groups)
    accounts = build_accounts(g, members, mock_key, mock_members)
    write_account_list(members, out / "synthetic_account_list.xlsx")
    write_incoming(g, accounts, args.rows, out)
    print(f"{len(members)} members, {len(accounts)} Medline accounts, {args.rows} invoice lines -> {out}")


if __name__ == "__main__":
    main()
