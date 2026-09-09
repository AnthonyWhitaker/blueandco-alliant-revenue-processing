"""
One-time roster expansion: adds facilities referenced by the Airgas rebate
file that are not yet in the member roster (Account List), so Airgas
reconciles to its deposit amount instead of leaving ~40% of the file in the
Review queue.

Derived from a full re-run of the Airgas file against the current roster
(see conversation/status report) — the 9 distinct legitimate payer names
below account for the full $4,303.32 unmatched gap except a $12.08 line
under "TUNISIA CORNELIUS", which reads like a person's name rather than a
facility and is deliberately left out of the roster; it will still surface
as a Review item for a human to confirm/dismiss/rename.

Two of the ten unmatched groups ("OWENSBORO HEALTH MUHLENBERG" and
"OWENSBORO HLTH MUHLENBERG COMM HOSP") are the same facility under two
spellings — one member is created and the second spelling is added as an
alias rather than a duplicate member.

Run from revenue_engine/:
    python scripts/add_airgas_roster_members.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.db import init_db
from engine.member_loader import create_member
from engine.normalizer import normalize_key, normalize_name

NEW_MEMBERS = [
    dict(name="US Renal Care Inc", city=None, state=None),
    dict(name="Beaumont Urgent Care by WellStreet", city=None, state=None),
    dict(name="Owensboro Health Muhlenberg", city="Greenville", state="KY"),
    dict(name="CCMH Corp", city="Carrollton", state="KY"),
    dict(name="Van Buren County Hospital", city="Clinton", state="AR"),
    dict(name="Advanced Houston", city="Houston", state="TX"),
    dict(name="Franklin Hospital & Skilled Nurse", city="Benton", state="IL"),
    dict(name="Bethany Bend Urgent Care", city="Alpharetta", state="GA"),
]

# raw_name -> matches which NEW_MEMBERS["name"] via alias
ALIASES = [
    ("OWENSBORO HLTH MUHLENBERG COMM HOSP", "Owensboro Health Muhlenberg"),
]


def main():
    conn = init_db()

    name_to_id = {}
    for m in NEW_MEMBERS:
        existing = conn.execute(
            "SELECT id FROM member WHERE canonical_key = ?",
            (normalize_name(m["name"]),),
        ).fetchone()
        if existing:
            print(f"  Skipping '{m['name']}' — already exists (id={existing['id']})")
            name_to_id[m["name"]] = existing["id"]
            continue
        member_id = create_member(
            conn, name=m["name"], city=m.get("city"), state=m.get("state"),
        )
        name_to_id[m["name"]] = member_id
        print(f"  Created '{m['name']}' -> member_id={member_id}")

    for raw_name, target_name in ALIASES:
        member_id = name_to_id[target_name]
        conn.execute(
            """
            INSERT OR IGNORE INTO member_alias
                (member_id, supplier_profile_id, raw_name, canonical_key, approved_by, approved_at)
            VALUES (?, NULL, ?, ?, 'seed_script', CURRENT_TIMESTAMP)
            """,
            (member_id, raw_name, normalize_key(raw_name)),
        )
        print(f"  Aliased '{raw_name}' -> member_id={member_id}")

    conn.commit()
    print(f"\n{len(NEW_MEMBERS)} members ensured, {len(ALIASES)} alias(es) added.")
    print("Left out: 'TUNISIA CORNELIUS' ($12.08, 6 rows) — looks like a person's "
          "name, not a facility; will still appear in Review for a human decision.")


if __name__ == "__main__":
    main()
