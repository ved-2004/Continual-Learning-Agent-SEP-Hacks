"""Export run results for the UI.

Reads data/ and two finished runs, and writes ui/demo.js (window.DEMO_DATA) and ui/demo.json.
Never reads data/eval/, so no hidden fact can reach the page.

    python ui/export.py                       # runs/learn and runs/nolearn
    python ui/export.py --learn learn_s1 --nolearn nolearn_s1
"""
import argparse
import json
import re
import pathlib
import datetime as dt

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Display only: tickets whose title asks someone to approve or choose. The agent never sees this label.
DECISION = re.compile(r"^(Review|Design review|Approve|Decide|Choose)\b")


def load(path):
    return json.loads(pathlib.Path(path).read_text())


def unwrap(obj, key):
    return obj[key] if isinstance(obj, dict) and key in obj else obj


def replay(history, days):
    """Ticket state after each day's Jira changes: {day: {ticket_id: ticket}}."""
    tickets = {t["id"]: dict(t) for t in history["tickets"]}
    states = {}
    for day in days:
        for ch in day["jira"]:
            if ch["action"] == "create":
                t = {"id": ch["ticket"], "description": "", "goal": None, "customer": None,
                     "due": None, "estimate_hours": None}
                t.update(ch["set"])
                tickets[ch["ticket"]] = t
            else:
                tickets[ch["ticket"]].update(ch["set"])
        states[day["day"]] = {k: dict(v) for k, v in tickets.items()}
    return tickets, states


def item_acceptance(feedback):
    shares = [len(set(f["proposed"]) & set(f["final"])) / len(f["proposed"]) for f in feedback if f["proposed"]]
    return round(100 * sum(shares) / len(shares), 1) if shares else None


def summary(run_dir, n):
    d = run_dir / f"day_{n:02d}"
    if not (d / "score.json").exists():
        return None
    score = load(d / "score.json")
    fb = unwrap(load(d / "feedback.json"), "feedback") if (d / "feedback.json").exists() else []
    return {"mean": score["mean"], "accepts": score["accepts"], "item_acceptance": item_acceptance(fb)}


def lesson_changes(before, after, n):
    old = {l["id"]: l for l in before}
    out = []
    for l in after:
        prev = old.get(l["id"])
        if prev and prev["rule"] == l["rule"] and prev.get("applies_to") == l.get("applies_to"):
            continue
        tag = f"D{n}:"
        from_people = [e[len(tag):] for e in l.get("evidence", []) if e.startswith(tag)]
        out.append({"kind": "revised" if prev else "added", "id": l["id"], "rule": l["rule"],
                    "before": prev["rule"] if prev else None, "applies_to": l.get("applies_to", []),
                    "from": from_people})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--runs", default=str(ROOT / "runs"))
    ap.add_argument("--learn", default="learn")
    ap.add_argument("--nolearn", default="nolearn")
    ap.add_argument("--out", default=str(ROOT / "ui"))
    a = ap.parse_args()

    data, runs, out = pathlib.Path(a.data), pathlib.Path(a.runs), pathlib.Path(a.out)
    purpose, history = load(data / "purpose.json"), load(data / "history.json")
    days = [load(p) for p in sorted((data / "days").glob("day_*.json"))]
    tickets, states = replay(history, days)
    learn, nolearn = runs / a.learn, runs / a.nolearn
    story_path = out / "story.json"
    story = load(story_path) if story_path.exists() else {}
    order = [p["id"] for p in history["people"]]

    result_days, prev_lessons = [], []
    for day in days:
        n = day["day"]
        d = learn / f"day_{n:02d}"
        if not (d / "plan.json").exists():
            break
        plans = {p["person"]: p for p in unwrap(load(d / "plan.json"), "plans")}
        feedback = {f["person"]: f for f in unwrap(load(d / "feedback.json"), "feedback")}
        score = load(d / "score.json")
        lessons = unwrap(load(d / "lessons.json"), "lessons") if (d / "lessons.json").exists() else []
        state = states[n]
        goal_hours = {}
        people = {}
        for pid in order:
            p = plans[pid]
            for item in p["top3"]:
                g = state[item["ticket"]].get("goal") or "none"
                goal_hours[g] = goal_hours.get(g, 0) + float(item["hours"])
            f = feedback.get(pid, {})
            people[pid] = {
                "items": [{"ticket": i["ticket"], "hours": i["hours"], "why": i.get("why", "")} for i in p["top3"]],
                "lessons_used": p.get("lessons_used", []),
                "note": p.get("note", ""),
                "feedback": {"action": f.get("action"), "final": f.get("final"), "reason": f.get("reason")},
                "score": score.get("people", {}).get(pid),
            }
        date = dt.date.fromisoformat(day["date"])
        result_days.append({
            "day": n, "date": day["date"], "weekday": date.strftime("%A"), "label": date.strftime("%B %-d"),
            "story": story.get(str(n), ""),
            "learn": summary(learn, n), "nolearn": summary(nolearn, n),
            "goal_hours": goal_hours,
            "plans": people,
            "open": [{"id": i, "owner": t["owner"]} for i, t in sorted(state.items(), key=lambda kv: int(kv[0].split("-")[1]))
                     if t["status"] != "done"],
            "brain": load(d / "brain.json") if (d / "brain.json").exists() else None,
            "lessons_after": lessons,
            "lesson_changes": lesson_changes(prev_lessons, lessons, n),
        })
        prev_lessons = lessons

    demo = {
        "company": {"name": purpose["company"], "mission": purpose["mission"], "what_we_do": purpose.get("what_we_do", ""),
                    "goals": [{"id": g["id"], "rank": g["rank"], "goal": g["goal"], "metric": g.get("metric", ""), "due": g.get("due")}
                              for g in sorted(purpose["goals"], key=lambda g: g["rank"])]},
        "people": [{"id": p["id"], "name": p["name"], "first": p["name"].split()[0], "role": p["role"]} for p in history["people"]],
        "tickets": {i: {"title": t["title"], "goal": t.get("goal"), "customer": t.get("customer"), "owner": t["owner"],
                        "kind": "decision" if DECISION.match(t["title"]) else "action"}
                    for i, t in tickets.items()},
        "runs": {"learn": a.learn, "nolearn": a.nolearn},
        "days": result_days,
    }
    text = json.dumps(demo, indent=1, ensure_ascii=False)
    (out / "demo.json").write_text(text + "\n")
    (out / "demo.js").write_text("window.DEMO_DATA = " + text + ";\n")
    print(f"wrote {out / 'demo.js'}: {len(result_days)} days, runs {a.learn} and {a.nolearn}")


if __name__ == "__main__":
    main()
