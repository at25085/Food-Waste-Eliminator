"""End-to-end check of a running deployment: every step prints PASS / FAIL / WARN.

    python scripts/e2e_check.py http://127.0.0.1:8000 --token YOUR_API_TOKEN [--retrain] [--store E2E_Test]

It creates a test store, uploads the sample sheets, and exercises plans, CSVs, the Timescale
series, briefing, store chat, manager notes, model cards, the write lock and (with --retrain) a
live retrain. It writes to the target database: run it on a copy, or reset afterwards (re-run
forecaster.db.copy_to_postgres).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool | None, detail: str = "") -> bool:
    status = "PASS" if ok else ("WARN" if ok is None else "FAIL")
    results.append((status, name, detail))
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--token", required=True)
    ap.add_argument("--store", default="E2E_Test")
    ap.add_argument("--retrain", action="store_true")
    ap.add_argument("--skip-uploads", action="store_true", help="store already created and uploaded")
    a = ap.parse_args()
    B, S = a.base.rstrip("/"), a.store
    auth = {"Authorization": f"Bearer {a.token}"}
    c = httpx.Client(timeout=120)

    # 1. Service up, integrations
    r = c.get(f"{B}/")
    check("dashboard served", r.status_code == 200 and "<div id=\"root\">" in r.text, f"HTTP {r.status_code}")
    integ = c.get(f"{B}/api/integrations").json()
    check("database is PostgreSQL", integ.get("database") == "postgresql", str(integ.get("database")))
    ts = integ.get("timescale") or {}
    check("TimescaleDB healthy", bool(ts.get("healthy")), f"hypertables={ts.get('hypertables')}")
    print("   integrations:", {k: v for k, v in integ.items() if k not in ("timescale",)}, flush=True)

    # 2. Write lock
    r = c.post(f"{B}/api/stores", json={"store_id": "Nope", "city": "x", "lat": 0, "lon": 0})
    check("write without token is refused", r.status_code in (401, 403), f"HTTP {r.status_code}")

    # 3. Create the test store (idempotent)
    r = None if a.skip_uploads else c.post(f"{B}/api/stores", headers=auth, json={
        "store_id": S, "city": "Prague", "lat": 50.0755, "lon": 14.4378, "timezone": "Europe/Prague",
        "country": "CZ", "kind": "demo"})
    if r is not None:
        check("create test store", r.status_code in (200, 201, 409), f"HTTP {r.status_code}")

    # 4. Uploads: history, then two weeks (a week is graded if the serving model never trained on it)
    for f in ([] if a.skip_uploads else ["1_history_import.csv", "2_week_may06.csv", "3_week_may20.csv"]):
        t = time.time()
        with open(SAMPLES / f, "rb") as fh:
            r = c.post(f"{B}/api/stores/{S}/uploads", headers=auth, files={"file": (f, fh, "text/csv")})
        d = r.json() if r.status_code == 200 else {}
        ev = d.get("evaluation") or {}
        check(f"upload {f}", r.status_code == 200 and d.get("accepted", 0) > 0,
              f"HTTP {r.status_code}, accepted {d.get('accepted')}, quarantined {d.get('quarantined')}, "
              f"graded {ev.get('evaluated_rows')}, {time.time() - t:.1f}s" + ("" if r.status_code == 200 else f" {r.text[:200]}"))
    if not a.skip_uploads:
        with open(SAMPLES / "4_batches.csv", "rb") as fh:
            r = c.post(f"{B}/api/stores/{S}/batches", headers=auth, files={"file": ("4_batches.csv", fh, "text/csv")})
        check("upload batch sheet", r.status_code == 200, f"HTTP {r.status_code}, accepted {(r.json() if r.status_code == 200 else {}).get('accepted')}")

    # 5. Plan and downloads
    t = time.time()
    r = c.get(f"{B}/api/stores/{S}/recommendations")
    rec = r.json() if r.status_code == 200 else {}
    items = rec.get("items", [])
    check("tomorrow's plan", r.status_code == 200 and len(items) > 0,
          f"{len(items)} products for {rec.get('forecast_date')}, model {rec.get('model_version')}, real stock {rec.get('real_stock')}, "
          f"customers/day {rec.get('recent_customers_7d')}, {time.time() - t:.1f}s")
    md = [i for i in items if i.get("markdown_schedule")]
    check("markdown / donation plan present", None if not md else True, f"{len(md)} products with a markdown plan")
    for path in [f"/api/stores/{S}/plan.csv", f"/api/stores/{S}/sell-through.csv", "/api/ledger.csv?context=store_eval",
                 "/api/uploads/template.csv"]:
        r = c.get(f"{B}{path}")
        check(f"download {path.split('?')[0].rsplit('/', 1)[-1]}", r.status_code == 200 and len(r.content) > 50,
              f"HTTP {r.status_code}, {len(r.content):,} bytes")

    # 6. Learning status and Timescale series
    r = c.get(f"{B}/api/stores/{S}/learning")
    check("learning status", r.status_code == 200, f"HTTP {r.status_code}")
    tsr = c.get(f"{B}/api/metrics/timeseries", params={"context": "store_eval", "store": S}).json()
    check("graded days from Timescale aggregate", len(tsr.get("days", [])) > 0 and "timescale" in (tsr.get("source") or ""),
          f"{len(tsr.get('days', []))} days, source: {tsr.get('source')}")

    # 7. Manager note → Backboard
    r = c.post(f"{B}/api/stores/{S}/notes", headers=auth, json={"content": "E2E test: street festival Saturday", "kind": "event"})
    n = r.json() if r.status_code == 200 else {}
    check("manager note saved + mirrored to Backboard", r.status_code == 200 and n.get("mirrored_to_backboard"),
          f"HTTP {r.status_code} {n}")

    # 8. Briefing (Gemini) + store chat
    t = time.time()
    r = c.get(f"{B}/api/stores/{S}/briefing")
    b = r.json() if r.status_code == 200 else {}
    prov = str(b.get("provider"))
    check("briefing written by Gemini", r.status_code == 200 and prov.startswith("gemini"),
          f"provider {prov}, {time.time() - t:.1f}s: {str(b.get('text'))[:110]!r}")
    check("briefing uses Backboard notes", None if not b.get("notes_source") else b.get("notes_source") == "backboard",
          f"notes_source {b.get('notes_source')}")

    t = time.time()
    r = c.post(f"{B}/api/stores/{S}/chat", json={"message": "What should I put on discount tomorrow?", "history": []})
    ch = r.json() if r.status_code == 200 else {}
    check("store chat answers from the data", r.status_code == 200 and str(ch.get("provider")).startswith("gemini"),
          f"{ch.get('provider')}, {time.time() - t:.1f}s: {str(ch.get('reply'))[:110]!r}")

    # 9. Model health + card
    h = c.get(f"{B}/api/model-health").json()
    champ = h.get("champion")
    check("model health", bool(champ), f"champion {champ}")
    r = c.get(f"{B}/api/model-cards/{champ}")
    card = r.json() if r.status_code == 200 else {}
    check("model card", r.status_code == 200 and card.get("_id") == champ, f"HTTP {r.status_code}")

    # 10. Retrain (optional, slow) and rollback
    if a.retrain:
        t = time.time()
        r = c.post(f"{B}/api/stores/{S}/retrain", headers=auth)
        check("retrain started", r.status_code == 200, f"HTTP {r.status_code}")
        job = {}
        while time.time() - t < 1800:
            time.sleep(20)
            job = c.get(f"{B}/api/stores/{S}/learning").json().get("last_retrain") or {}
            if job.get("state") in ("done", "failed"):
                break
        check("retrain finished", job.get("state") == "done",
              f"{time.time() - t:.0f}s, decision {job.get('decision')}, champion WAPE {job.get('champion_wape')}, "
              f"challenger WAPE {job.get('challenger_wape')}, reason: {str(job.get('reason') or job.get('error'))[:200]}")
        new = c.get(f"{B}/api/model-health").json().get("champion")
        if new != champ:
            r = c.post(f"{B}/api/models/{champ}/rollback", headers=auth)
            check("rollback to previous champion", r.status_code == 200 and r.json().get("champion") == champ, r.text[:150])
            back = c.get(f"{B}/api/model-health").json().get("champion")
            check("champion restored", back == champ, f"{back}")

    print("\nSummary:", {s: sum(1 for x in results if x[0] == s) for s in ("PASS", "WARN", "FAIL")})
    return 1 if any(s == "FAIL" for s, _, _ in results) else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
