from pathlib import Path
from agent.schemas import EvalDay
from agent.store import load_raw, parse

def load_evals(root, history, states, errors):
    root = Path(root)
    paths = sorted((root / "eval").glob("*.json"))
    expected = {p.name for p in (root / "days").glob("*.json")}
    actual = {p.name for p in paths}
    for name in sorted(expected ^ actual):
        errors.setdefault(str(root / "eval" / name), []).append("day/eval file pair missing")
    evals = {}
    for path in paths:
        value = parse(EvalDay, load_raw(path, errors), path, errors)
        if value is None:
            continue
        evals[value.day] = value
        problems = []
        if path.name != f"day_{value.day:02}.json":
            problems.append("day must equal file number")
        people = [r.person for r in value.records]
        if history and (len(people) != 6 or set(people) != {p.id for p in history.people}):
            problems.append("expected exactly one eval record per person")
        for record in value.records:
            if len(record.final) != 3 or len(set(record.final)) != 3:
                problems.append(f"{record.person}: final must contain 3 distinct tickets")
            for ticket in record.final:
                state = states.get(value.day, {})
                if ticket not in state or state[ticket].status == "done":
                    problems.append(f"{record.person}: final ticket {ticket} missing or done")
            if not record.reason.strip() or not record.hidden_fact.strip():
                problems.append(f"{record.person}: reason and hidden_fact must not be empty")
        if problems:
            errors.setdefault(str(path), []).extend(problems)
    facts = [r.hidden_fact for day in evals.values() for r in day.records]
    for path in (root / "days").glob("*.json"):
        raw = load_raw(path, errors)
        def strings(value):
            if isinstance(value, str):
                return [value]
            if isinstance(value, list):
                return [s for item in value for s in strings(item)]
            if isinstance(value, dict):
                return [s for item in value.values() for s in strings(item)]
            return []
        if any(len(f) >= 20 and any(f in text for text in strings(raw)) for f in facts):
            errors.setdefault(str(path), []).append("hidden_fact appears in day data")
    for day in evals.values():
        if any(len(f) >= 20 and f in r.reason for f in facts for r in day.records):
            errors.setdefault(str(root / "eval" / f"day_{day.day:02}.json"), []).append("hidden_fact appears in reason")
    return evals, facts

WEIGHTS = (3, 2, 1)

def score_person(proposed, final):
    earned = sum(WEIGHTS[i] if proposed[i] == ticket else WEIGHTS[i] / 2 if ticket in proposed else 0
                 for i, ticket in enumerate(final))
    return round(100 * earned / 6, 1)

def simulate_feedback(plan, eval_day):
    from agent.schemas import Feedback
    records = {r.person: r for r in eval_day.records}
    feedback = []
    for person in plan.plans:
        proposed, record = [item.ticket for item in person.top3], records[person.person]
        edited = proposed != record.final
        feedback.append(Feedback(person=person.person, action="edit" if edited else "accept",
                                 proposed=proposed, final=record.final, reason=record.reason if edited else None))
    return feedback

def score_day(plan, eval_day, date):
    from agent.schemas import DayScore
    feedback = simulate_feedback(plan, eval_day)
    people = {f.person: score_person(f.proposed, f.final) for f in feedback}
    return DayScore(day=eval_day.day, date=date, mean=round(sum(people.values()) / len(people), 1),
                    accepts=sum(f.action == "accept" for f in feedback), people=people)
