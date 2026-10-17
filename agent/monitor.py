"""Repaired outputs failed checks before a successful retry. Rejected plans score
below 50. Regressions drop the team mean by at least 15 points since yesterday.
A lesson is overridden when someone cites it but edits their plan anyway.
These signals describe reliability and disagreement, not the truth of a lesson.
"""
import json
from collections import Counter
from agent import store

def health(path, day):
    folder = path / f"day_{day:02}"
    score, plan, feedback = [store.read_json(folder / f"{name}.json") for name in ("score", "plan", "feedback")]
    edits = {f["person"] for f in feedback if f["action"] == "edit"}
    calls = [json.loads(line) for line in (path / "llm_calls.jsonl").read_text().splitlines()]
    totals = store.read_json(path / "lesson_health.json") if (path / "lesson_health.json").exists() else {}
    alerts = []
    def alert(kind, detail, person=None, lesson=None):
        alerts.append(dict(kind=kind, person=person, lesson=lesson, detail=detail))
    failures = {c["step"]: c["errors"] for c in calls if c["day"] == day and c["attempt"] == 1 and c.get("errors")}
    for c in calls:
        if c["day"] == day and c["attempt"] == 2 and not c.get("errors") and c["step"] in failures:
            alert("repaired", f"{c['step']}: {'; '.join(failures[c['step']])}")
    for person, value in score["people"].items():
        if value < 50:
            alert("rejected_plan", f"{person} scored {value:.1f}", person)
    if day > 1:
        before = store.read_json(path / f"day_{day - 1:02}/score.json")["mean"]
        if before - score["mean"] >= 15:
            alert("regression", f"mean {score['mean']:.1f}, down {before - score['mean']:.1f} from day {day - 1}")
    for person in plan["plans"]:
        for lesson in dict.fromkeys(person["lessons_used"]):
            total = totals.setdefault(lesson, dict(uses=0, overrides=0, flagged=False))
            total["uses"] += 1
            total["overrides"] += person["person"] in edits
            if person["person"] in edits:
                alert("lesson_overridden", f"{person['person']} edited despite using {lesson}", person["person"], lesson)
    for lesson in store.read_json(folder / "lessons.json"):
        totals.setdefault(lesson["id"], dict(uses=0, overrides=0, flagged=False))
    for lesson, total in totals.items():
        flagged = total["overrides"] >= 2 and total["overrides"] * 2 >= total["uses"]
        if flagged and not total["flagged"]:
            print(f"lesson {lesson} may be stale or wrong: overridden {total['overrides']} of {total['uses']} times")
        total["flagged"] = flagged
    store.write_json(folder / "health.json", dict(day=day, alerts=alerts))
    store.write_json(path / "lesson_health.json", totals)
    for a in alerts:
        print(f"ALERT day {day} {a['kind']}: {a['detail']}")

def report(paths):
    print("\nHEALTH\nrun day repaired rejected_plan regression lesson_overridden")
    for path in paths:
        for folder in sorted(path.glob("day_[0-9][0-9]")):
            if not (folder / "health.json").exists():
                print(f"{path.name} {int(folder.name[-2:])}: health unavailable; rerun from cache")
                continue
            value = store.read_json(folder / "health.json")
            counts = Counter(a["kind"] for a in value["alerts"])
            print(path.name, value["day"], *(counts[k] for k in ("repaired", "rejected_plan", "regression", "lesson_overridden")))
    for path in paths:
        folders = sorted(path.glob("day_[0-9][0-9]"))
        lessons = store.read_json(folders[-1] / "lessons.json")
        if not lessons:
            continue
        print(f"\nTRANSFER {path.name}\nlesson teachers later users accepted/uses")
        changed, accepted, transferred = set(), set(), set()
        for lesson in lessons:
            teachers = {e.split(":")[1] for e in lesson["evidence"]}
            uses, approvals = set(), set()
            for folder in folders:
                day = int(folder.name[-2:])
                feedback = {f["person"]: f["action"] for f in store.read_json(folder / "feedback.json")}
                for p in store.read_json(folder / "plan.json")["plans"]:
                    if day > lesson["created_day"] and lesson["id"] in p["lessons_used"]:
                        uses.add((day, p["person"]))
                        if feedback[p["person"]] == "accept":
                            approvals.add((day, p["person"]))
            changed.update(uses)
            accepted.update(approvals)
            transferred.update(pair for pair in uses if pair[1] not in teachers)
            print(lesson["id"], ",".join(sorted(teachers)), ",".join(sorted({p for _, p in uses})) or "none", f"{len(approvals)}/{len(uses)}")
        print(f"Lessons changed {len(changed)} plans on later days; {len(accepted)} were accepted unchanged; {len(transferred)} went to people who never taught that lesson.")
