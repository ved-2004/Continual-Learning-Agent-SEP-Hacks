"""Weight-learning baseline: a small linear ranker whose weights update from each day's corrections.

It sees only structured ticket fields, never text or reasons. Each day it ranks every open ticket for each
person and proposes the top 3. It is scored against that day's choices before it learns from them, like
the learn run, then retrains on all corrections so far with pairwise logistic updates.

    python baselines/ranker.py          # prints both curves, writes runs/ranker_fixed and runs/ranker_online scores.csv
No API calls, no dependencies, deterministic.
"""
import csv
import datetime as dt
import json
import math
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA, RUNS = ROOT / "data", ROOT / "runs"
WEIGHTS = (3, 2, 1)
PRIOR = {"prio_high": 3.0, "prio_medium": 2.0, "prio_low": 1.0, "due_today": 1.0, "due_tomorrow": 0.7,
         "due_soon": 0.3, "goal_G1": 0.6, "goal_G2": 0.4, "goal_G3": 0.2, "mine": 6.0, "in_progress": 0.3}


def score(proposed, final):
    earned = 0.0
    for i, t in enumerate(final):
        if proposed[i] == t:
            earned += WEIGHTS[i]
        elif t in proposed:
            earned += WEIGHTS[i] / 2
    return round(100 * earned / 6, 1)


def features(t, person, date, created_on, created_by):
    f = {"prio_" + t["priority"]: 1.0, f"goal_{t.get('goal')}": 1.0, f"cust_{t.get('customer')}": 1.0}
    if t.get("due"):
        d = (dt.date.fromisoformat(t["due"]) - dt.date.fromisoformat(date)).days
        f["due_" + ("today" if d <= 0 else "tomorrow" if d == 1 else "soon" if d <= 3 else "later")] = 1.0
    else:
        f["due_none"] = 1.0
    f["in_progress"] = float(t["status"] == "in_progress")
    f["mine"] = float(t["owner"] == person)
    f["new_today"] = float(created_on == date)
    f["assigned_by_other"] = float(bool(created_by) and created_by != t["owner"])
    f["small"] = float((t.get("estimate_hours") or 9) <= 1)
    return f


def dot(w, f):
    return sum(w.get(k, 0.0) * v for k, v in f.items())


def train(pairs, epochs=60, lr=0.15, l2=0.01):
    w = dict(PRIOR)
    for _ in range(epochs):
        for fa, fb in pairs:
            diff = {k: fa.get(k, 0) - fb.get(k, 0) for k in set(fa) | set(fb)}
            g = 1 - 1 / (1 + math.exp(-dot(w, diff)))
            for k, v in diff.items():
                w[k] = w.get(k, 0.0) + lr * (g * v - l2 * (w.get(k, 0.0) - PRIOR.get(k, 0.0)))
    return w


def run(learn):
    history = json.loads((DATA / "history.json").read_text())
    people = [p["id"] for p in history["people"]]
    tickets = {t["id"]: dict(t) for t in history["tickets"]}
    meta = {i: (history["as_of"], None) for i in tickets}
    w, pairs, rows = dict(PRIOR), [], []
    for path in sorted((DATA / "days").glob("day_*.json")):
        day = json.loads(path.read_text())
        ev = json.loads((DATA / "eval" / path.name).read_text())
        date = day["date"]
        for c in day["jira"]:
            if c["action"] == "create":
                tickets[c["ticket"]] = {"id": c["ticket"], "description": "", "goal": None, "customer": None,
                                        "due": None, "estimate_hours": None, **c["set"]}
                meta[c["ticket"]] = (date, c["by"])
            else:
                tickets[c["ticket"]].update(c["set"])
        open_ = [t for t in tickets.values() if t["status"] != "done"]
        finals = {r["person"]: r["final"] for r in ev["records"]}
        scores, new_pairs = {}, []
        for p in people:
            fs = {t["id"]: features(t, p, date, *meta[t["id"]]) for t in open_}
            ranked = sorted(fs, key=lambda i: (-dot(w, fs[i]), int(i.split("-")[1])))
            prop, fin = ranked[:3], finals[p]
            scores[p] = score(prop, fin)
            if prop != fin:
                for i, a in enumerate(fin):
                    new_pairs += [(fs[a], fs[b]) for b in fin[i + 1:]]
                    new_pairs += [(fs[a], fs[b]) for b in ranked[:8] if b not in fin]
        mean = round(sum(scores.values()) / len(people), 1)
        rows.append([day["day"], date, mean, sum(s == 100.0 for s in scores.values()), *[scores[p] for p in people]])
        if learn:
            pairs += new_pairs
            w = train(pairs)
    return people, rows, w


def main():
    out = {}
    for name, learn in (("ranker_fixed", False), ("ranker_online", True)):
        people, rows, w = run(learn)
        d = RUNS / name
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "scores.csv", "w", newline="") as fh:
            csv.writer(fh).writerows([["day", "date", "mean", "accepts", *people], *rows])
        (d / "weights.json").write_text(json.dumps(w, indent=2, sort_keys=True) + "\n")
        out[name] = (rows, w)
    fixed, online = out["ranker_fixed"][0], out["ranker_online"][0]
    print(f"{'day':>3}  {'fixed weights':>13}  {'learned weights':>15}")
    for a, b in zip(fixed, online):
        print(f"{a[0]:>3}  {a[2]:>13}  {b[2]:>15}")
    w = out["ranker_online"][1]
    print("largest weight changes from the starting weights:")
    for k in sorted(w, key=lambda k: -abs(w[k] - PRIOR.get(k, 0.0)))[:6]:
        print(f"  {k:18} {PRIOR.get(k, 0.0):+.2f} -> {w[k]:+.2f}")


if __name__ == "__main__":
    main()
