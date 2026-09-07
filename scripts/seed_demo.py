"""Populate the repository with demonstrable inspection history.

Three things need data before they mean anything: the dashboard, the
shrinkflation watch, and the officer workflow. The shrinkflation watch is the
point of the exercise -- it is a finding that no single scan can produce, and
it falls out of the repository the problem statement already requires.

Every record here goes through the real pipeline. Nothing is written straight
to the database, so if the extractor or the rules engine regress, the seed
output changes with them.

Each label is also rendered as a real PNG at the cap height its ground truth
claims, so the retained evidence verifies by hash. That is what lets the seeded
records walk the actual review workflow -- decide, submit, approve -- instead of
sitting in draft. The dashboard's approved-compliance rate counts only approved
records, so without that walk every headline figure reads zero.

    python scripts/seed_demo.py
    python scripts/seed_demo.py --reset      # discard existing records first
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tula.analyse import AnalyseOptions, Capture, analyse
from tula.domain.enums import Lane, Panel, Verdict
from tula.labgen import _font_for_cap_height
from tula.ocr.engines import FixtureEngine
from tula.rules.engine import RulesEngine
from tula.security import SecurityStore
from tula.services import review
from tula.services.context import form_context
from tula.storage.db import Repository

WORK = ROOT / "data" / "seed"

# Demonstration accounts. The password is deliberately visible: these exist to
# make a local walkthrough possible, and every one of them must be removed or
# repasswordded before the application is exposed beyond loopback.
DEMO_PASSWORD = "TulaDemo2026!judge"
ACCOUNTS = [
    ("demo.admin", "Demo Administrator", "admin"),
    ("demo.inspector", "R. Krishnan (Inspector)", "inspector"),
    ("demo.supervisor", "A. Sharma (Supervisor)", "supervisor"),
]


def label(
    *,
    brand: str,
    generic: str,
    qty: str,
    mrp: str,
    unit_price: str | None,
    mfr: str,
    address: str,
    date: str,
    care: str | None,
    origin: str | None,
    gtin: str | None,
    hindi: bool = False,
    qty_box_px: int = 56,
) -> str:
    """Compose a fixture label. Geometry matters: the net-quantity box height
    drives the Rule 8 measurement, so a small box is a real violation."""
    y = 120
    rows: list[str] = []

    def add(text: str, height: int = 50) -> None:
        nonlocal y
        rows.append(f"100,{y},{100 + 12 * len(text)},{y + height}|{text}")
        y += height + 24

    add(brand, 80)
    add(generic)
    # both renderings of the net quantity are set at the same height, as a
    # real label would; the engine measures the smallest of them
    if hindi:
        rows.append(f"100,{y},520,{y + qty_box_px}|शुद्ध वजन {qty}")
        y += qty_box_px + 24
    rows.append(f"100,{y},520,{y + qty_box_px}|Net Wt. {qty}")
    y += qty_box_px + 24
    add(f"MRP {mrp}")
    if unit_price:
        add(f"Unit Sale Price: {unit_price}")
    add(f"Manufactured by: {mfr}")
    add(address)
    add(f"Mfg: {date}")
    if care:
        add(f"Consumer Care: {care}")
    if origin:
        add(origin)
    if gtin:
        add(gtin)
    return "\n".join(rows) + "\n"


def meta(packing: str, *, width_mm=120, height_mm=180, mm_per_px=0.0412) -> str:
    return (
        f'{{"panel": "pdp", "mm_per_px": {mm_per_px}, "mm_per_px_source": "device_depth",'
        f' "pdp_width_mm": {width_mm}, "pdp_height_mm": {height_mm}, "packing_date": "{packing}"}}'
    )


def render_png(sidecar: str, path: Path) -> None:
    """Draw the fixture rows as a real image at the cap height they declare.

    The fixture engine reads its text from the sidecar, so this image does not
    feed extraction. It exists so the retained evidence hashes, verifies and
    crops -- and so the picture an officer sees actually shows the glyph sizes
    the metrology reports. Text is set at `CAP_FRACTION` of each declared box,
    which is the same ratio the fixture engine infers.
    """
    rows = [r for r in sidecar.splitlines() if r.strip() and not r.startswith("#")]
    boxes = []
    for row in rows:
        geom, _, text = row.partition("|")
        x0, y0, x1, y1 = (int(float(v)) for v in geom.split(",")[:4])
        boxes.append((x0, y0, x1, y1, text))

    margin = 90
    width = max(x1 for _, _, x1, _, _ in boxes) + margin * 2
    height = max(y1 for _, _, _, y1, _ in boxes) + margin
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    for index, (x0, y0, _, y1, text) in enumerate(boxes):
        cap = (y1 - y0) * FixtureEngine.CAP_FRACTION
        font = _font_for_cap_height(index == 0, cap, text)
        draw.text((x0, y0), text, fill="#101418", font=font, anchor="lt")
    canvas.save(path)


# Coordinates are city centroids, not real inspection sites. They exist so the
# dashboard's coordinate plot and its drill-down have something to plot.
CASES = [
    # --- the shrinkflation series: same GTIN, same MRP, falling quantity ----
    {"key": "namkeen-2024", "captured": "2024-05-12", "packing": "2024-04-01",
         "qty": "100 g", "mrp": "Rs. 20.00 inclusive of all taxes",
         "unit_price": "Rs. 0.20 per g", "brand": "Bikaner Ratan", "generic": "Namkeen",
         "gtin": "8901234567890", "hindi": True, "qty_box_px": 110,
         "region": "Pune", "geo": (18.5204, 73.8567), "settle": "approve",
         "context": {"category": "food", "shape": "other"}},
    {"key": "namkeen-2025", "captured": "2025-06-18", "packing": "2025-05-01",
         "qty": "92 g", "mrp": "Rs. 20.00 inclusive of all taxes",
         "unit_price": "Rs. 0.22 per g", "brand": "Bikaner Ratan", "generic": "Namkeen",
         "gtin": "8901234567890", "hindi": True, "qty_box_px": 110,
         "region": "Pune", "geo": (18.5204, 73.8567), "settle": "approve",
         "context": {"category": "food", "shape": "other"}},
    {"key": "namkeen-2026", "captured": "2026-07-02", "packing": "2026-06-01",
         "qty": "85 g", "mrp": "Rs. 20.00 inclusive of all taxes",
         "unit_price": "Rs. 0.24 per g", "brand": "Bikaner Ratan", "generic": "Namkeen",
         "gtin": "8901234567890", "hindi": True, "qty_box_px": 110,
         "region": "Pune", "geo": (18.5204, 73.8567), "settle": "approve",
         "context": {"category": "food", "shape": "other"}},

    # --- a clean pack, to prove the system is not just a violation printer --
    {"key": "tea-clean", "captured": "2026-08-04", "packing": "2026-05-01",
         "qty": "250 g", "mrp": "Rs. 145.00 inclusive of all taxes",
         "unit_price": "Rs. 0.58 per g", "brand": "Nilgiri Estate", "generic": "Tea",
         "gtin": "8901234567890", "hindi": True, "qty_box_px": 110,
         "mfr": "Nilgiri Tea Estates Ltd", "address": "Survey 88, Coonoor Road, Ooty 643001",
         "region": "Coimbatore", "geo": (11.0168, 76.9558), "settle": "approve",
         "context": {"category": "food", "shape": "rectangular"}},

    # --- sub-millimetre print on a large panel -----------------------------
    {"key": "detergent-tiny", "captured": "2026-08-09", "packing": "2026-06-01",
         "qty": "1 kg", "mrp": "Rs. 199.00 inclusive of all taxes",
         "unit_price": "Rs. 0.20 per g", "brand": "Sparkle Max", "generic": "Detergent powder",
         "gtin": None, "hindi": True, "qty_box_px": 34, "width_mm": 200, "height_mm": 300,
         "region": "Nagpur", "geo": (21.1458, 79.0882), "settle": "approve",
         "context": {"category": "general", "shape": "rectangular"}},

    # --- exempt: a sub-10 g sachet -----------------------------------------
    {"key": "shampoo-sachet", "captured": "2026-08-11", "packing": "2026-07-01",
         "qty": "6 ml", "mrp": "Rs. 3.00 inclusive of all taxes", "unit_price": None,
         "brand": "Silk Shine", "generic": "Shampoo", "gtin": None, "qty_box_px": 40,
         "region": "Jaipur", "geo": (26.9124, 75.7873), "settle": "approve",
         "context": {"category": "cosmetic", "shape": "other"}},

    # --- English only, non-standard unit, no tax clause --------------------
    {"key": "oil-adversarial", "captured": "2026-08-14", "packing": "2026-06-01",
         "qty": "500 ML", "mrp": "Rs. 168.00", "unit_price": "Rs. 30.00 per 100 ml",
         "brand": "Golden Drop", "generic": "Mustard oil", "gtin": "6901234567892",
         "origin": "Made in India", "qty_box_px": 52,
         "region": "Kolkata", "geo": (22.5726, 88.3639), "settle": "submit",
         "context": {"category": "food", "shape": "cylindrical"}},

    # --- recent captures, so the trend and "this week" figures are alive ---
    {"key": "biscuit-delhi", "captured": "2026-09-01", "packing": "2026-07-15",
         "qty": "200 g", "mrp": "Rs. 45.00 inclusive of all taxes",
         "unit_price": "Rs. 0.23 per g", "brand": "Suraj Gold", "generic": "Biscuits",
         "gtin": "8904321567890", "hindi": True, "qty_box_px": 96,
         "mfr": "Suraj Foods Ltd", "address": "Sector 63, Noida 201301",
         "region": "New Delhi", "geo": (28.6139, 77.2090), "settle": "approve",
         "context": {"category": "food", "shape": "rectangular"}},
    {"key": "atta-bengaluru", "captured": "2026-09-03", "packing": "2026-08-02",
         "qty": "5 kg", "mrp": "Rs. 285.00 inclusive of all taxes",
         "unit_price": "Rs. 57.00 per kg", "brand": "Annapurna Mills", "generic": "Wheat flour",
         "gtin": "8905671234567", "hindi": True, "qty_box_px": 120,
         "mfr": "Annapurna Mills Pvt Ltd", "address": "Peenya Industrial Area, Bengaluru 560058",
         "width_mm": 220, "height_mm": 340,
         "region": "Bengaluru", "geo": (12.9716, 77.5946), "settle": "approve",
         "context": {"category": "food", "shape": "rectangular"}},
    {"key": "soap-chennai", "captured": "2026-09-05", "packing": "2026-07-20",
         "qty": "100 g", "mrp": "Rs. 62.00", "unit_price": None,
         "brand": "Neem Fresh", "generic": "Bathing soap", "gtin": "8907654321098",
         "qty_box_px": 44, "care": None,
         "mfr": "Neem Care Products Ltd", "address": "Ambattur Industrial Estate, Chennai 600058",
         "region": "Chennai", "geo": (13.0827, 80.2707), "settle": "approve",
         "context": {"category": "cosmetic", "shape": "rectangular"}},
    {"key": "spices-hyderabad", "captured": "2026-09-06", "packing": "2026-08-11",
         "qty": "50 g", "mrp": "Rs. 38.00 inclusive of all taxes",
         "unit_price": "Rs. 0.76 per g", "brand": "Deccan Spice", "generic": "Chilli powder",
         "gtin": "8908765432109", "hindi": True, "qty_box_px": 62,
         "mfr": "Deccan Spice Company", "address": "Balanagar, Hyderabad 500037",
         "region": "Hyderabad", "geo": (17.3850, 78.4867), "settle": "submit",
         "context": {"category": "food", "shape": "other"}},
    {"key": "ghee-lucknow", "captured": "2026-09-06", "packing": "2026-08-18",
         "qty": "500 ml", "mrp": "Rs. 340.00 inclusive of all taxes",
         "unit_price": "Rs. 0.68 per ml", "brand": "Awadh Pure", "generic": "Ghee",
         "gtin": "8909876543210", "hindi": True, "qty_box_px": 88,
         "mfr": "Awadh Dairy Ltd", "address": "Chinhat Industrial Area, Lucknow 226028",
         "region": "Lucknow", "geo": (26.8467, 80.9462), "settle": "none",
         "context": {"category": "food", "shape": "cylindrical"}},
]


def ensure_accounts(store: SecurityStore) -> dict[str, object]:
    """Provision the walkthrough accounts, reusing any that already exist."""
    existing = {u.username: u for u in store.list_users()}
    admin = next((u for u in existing.values() if u.role == "admin" and u.active), None)
    users: dict[str, object] = {}
    for username, display_name, role in ACCOUNTS:
        if username in existing:
            users[role] = existing[username]
            continue
        if admin is None:
            user = store.create_user(username, DEMO_PASSWORD, display_name=display_name,
                                     role="admin", bootstrap=True)
            admin = user
            users["admin"] = user
            print(f"  bootstrapped administrator  {username}")
            continue
        user = store.create_user(username, DEMO_PASSWORD, display_name=display_name,
                                 role=role, actor_id=admin.id)
        users[role] = user
        print(f"  created {role:11s} {username}")
    if "admin" not in users and admin is not None:
        users["admin"] = admin
    for role in ("inspector", "supervisor"):
        if role not in users:
            raise SystemExit(f"No {role} account is available; create one at /admin/users.")
    return users


def settle(repo, rules, analysis, inspector, supervisor, case) -> str:
    """Walk one record through the review workflow the officer console uses.

    Decisions are recorded through `services.review`, so every optimistic
    revision check, evidence verification and separation-of-duty rule that
    guards the real workflow also guards the seed. An approval that the
    application would refuse is refused here too.

    Confirming the package facts comes first, and it is not a formality: several
    rules are gated on a category the machine cannot read off a photograph, and
    until an officer attests to one they return "manual legal review required"
    rather than a verdict. Confirmation re-evaluates the pack, so it has to
    precede any finding decision -- which is why it clears them.
    """
    plan = case.get("settle", "none")
    if plan == "none":
        return "draft"
    scan_id = analysis.scan.scan_id

    context = case.get("context")
    if context:
        analysis = review.confirm_context(
            repo, rules, scan_id, analysis.review.revision, inspector,
            form_context(context["category"], context.get("bundle", "single"),
                         context.get("origin", "domestic"), context.get("shape", "rectangular"),
                         True),
            "Demonstration record: package facts confirmed from the retained image during seeding.")

    for finding in analysis.pending_review:
        # A machine violation the officer confirms; anything the machine could
        # not settle is recorded as verified by eye against the retained image.
        verdict = Verdict.VIOLATION if finding.verdict is Verdict.VIOLATION else Verdict.PASS
        reason = ("Demonstration record: confirmed against the retained evidence image during "
                  "seeding. Not an officer decision about a real product.")
        analysis = review.decide(repo, scan_id, analysis.review.revision, inspector,
                                 finding.finding_id, verdict, reason)

    analysis = review.transition(repo, scan_id, analysis.review.revision, inspector, "submit",
                                 "Demonstration record: submitted for supervisory review.")
    if plan == "submit":
        return "submitted"
    review.transition(repo, scan_id, analysis.review.revision, supervisor, "approve",
                      "Demonstration record: approved by a supervisor who did not contribute to it.")
    return "approved"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true",
                        help="delete existing inspection records before seeding")
    args = parser.parse_args()

    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True, exist_ok=True)

    database = ROOT / "data" / "tula.db"
    repo = Repository(database)
    rules = RulesEngine.from_directory()
    store = SecurityStore(database)
    users = ensure_accounts(store)
    inspector, supervisor = users["inspector"], users["supervisor"]

    if args.reset:
        removed = repo.reset_inspections() if hasattr(repo, "reset_inspections") else None
        if removed is None:
            import sqlite3
            with sqlite3.connect(database) as conn:
                conn.execute("PRAGMA foreign_keys=ON")
                for table in ("finding", "inspection_revision", "inspection"):
                    try:
                        conn.execute(f"DELETE FROM {table}")
                    except sqlite3.Error:
                        pass
        print("  cleared existing inspection records")

    outcomes: dict[str, int] = {}
    for case in CASES:
        stem = WORK / case["key"]
        sidecar = label(
            brand=case["brand"],
            generic=case["generic"],
            qty=case["qty"],
            mrp=case["mrp"],
            unit_price=case.get("unit_price"),
            mfr=case.get("mfr", "Gold Foods Pvt Ltd"),
            address=case.get("address", "Plot 42, MIDC Industrial Estate, Pune 411018"),
            date=case["packing"][5:7] + "/" + case["packing"][:4],
            care=case.get("care", "care@example.co.in, 1800 200 1234"),
            origin=case.get("origin"),
            gtin=case.get("gtin"),
            hindi=case.get("hindi", False),
            qty_box_px=case.get("qty_box_px", 56),
        )
        image = stem.with_suffix(".png")
        stem.with_suffix(".txt").write_text(sidecar, encoding="utf-8")
        stem.with_suffix(".meta.json").write_text(
            meta(case["packing"],
                 width_mm=case.get("width_mm", 120),
                 height_mm=case.get("height_mm", 180)),
            encoding="utf-8",
        )
        render_png(sidecar, image)

        analysis = analyse(
            [Capture(str(image), Panel.PDP)],
            AnalyseOptions(lane=Lane.FIELD, operator="INSP-KA-0114", engine_name="fixture",
                           geo=case.get("geo")),
            rules=rules,
        )
        # backdate the capture so the history reads as a real time series, and
        # attach the record to the inspector who will review it
        captured = datetime.fromisoformat(case["captured"]).replace(tzinfo=UTC)
        analysis.scan = analysis.scan.model_copy(update={
            "captured_at": captured,
            "inspector_id": inspector.id,
            "region": case.get("region", ""),
        })
        repo.save(analysis)
        state = settle(repo, rules, analysis, inspector, supervisor, case)
        outcomes[state] = outcomes.get(state, 0) + 1

        final = repo.get(analysis.scan.scan_id)
        print(
            f"  {case['key']:20s} {analysis.scan.scan_id}  "
            f"{final.overall.value:14s} "
            f"{len(final.violations)} violation(s), Tier {final.scan.tier.value}  {state}"
        )

    stats = repo.stats()
    analytics = repo.analytics()
    print()
    print(f"  {stats['inspections']} inspections · {stats['violations']} violations · "
          f"{stats['violation_rate']:.0f}% of packages failing")
    print("  workflow: " + " · ".join(f"{n} {state}" for state, n in sorted(outcomes.items())))
    print(f"  dashboard: {analytics['compliant']} approved compliant · "
          f"{analytics['non_compliant']} approved non-compliant · "
          f"{analytics['needs_review']} needing review")
    print(f"  accounts: demo.inspector / demo.supervisor / demo.admin  (password {DEMO_PASSWORD})")
    print(f"  database: {database}")
    print("  start the console with:  python -m uvicorn tula.web.app:app --reload")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
