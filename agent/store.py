import json
from copy import deepcopy
import re
from pathlib import Path
from pydantic import BaseModel, ValidationError
from agent.schemas import Comment, Day, History, JiraChange, Purpose, Ticket

def read_json(path):
    return json.loads(Path(path).read_text())

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    def encode(item):
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        raise TypeError(f"Cannot encode {type(item).__name__}")
    path.write_text(json.dumps(value, default=encode, indent=2) + "\n")

def parse(model, raw, path, errors):
    try:
        return model.model_validate(raw, context={"input": True})
    except ValidationError as exc:
        errors.setdefault(str(path), []).extend(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        # Keep diagnostics, but remove rejected extras to check the remaining references too.
        if all(e["type"] == "extra_forbidden" for e in exc.errors()):
            cleaned = deepcopy(raw)
            for error in exc.errors():
                parent = cleaned
                for key in error["loc"][:-1]:
                    parent = parent[key]
                del parent[error["loc"][-1]]
            return model.model_validate(cleaned, context={"input": True})

def load_raw(path, errors):
    try:
        return read_json(path)
    except (OSError, ValueError) as exc:
        errors.setdefault(str(path), []).append(str(exc))
        return None

def apply_change(tickets, change, day):
    patch = change.set.model_dump(exclude_unset=True)
    if change.action == "create":
        if change.ticket in tickets:
            raise ValueError(f"create: ticket {change.ticket} already exists")
        if not {"title", "owner", "status", "priority"} <= patch.keys():
            raise ValueError(f"create {change.ticket}: title, owner, status and priority required")
        value = dict(description="", goal=None, customer=None, due=None, estimate_hours=None)
    else:
        if change.ticket not in tickets:
            raise ValueError(f"update: ticket {change.ticket} does not exist")
        if not patch and change.comment is None:
            raise ValueError(f"update {change.ticket}: empty set and no comment")
        value = tickets[change.ticket].model_dump()
    value.update(patch)
    value["id"] = change.ticket
    ticket = Ticket.model_validate(value)
    if change.comment is not None:
        ticket.comments.append(Comment(day=day, by=change.by, text=change.comment))
    tickets[change.ticket] = ticket
    return ticket

def source_items(day):
    return [(f"D{day.day}-{letter}{i}", item) for letter, items in
            (("M", day.meetings), ("S", day.slack), ("J", day.jira)) for i, item in enumerate(items, 1)]

def check_references(raw, refs, path, errors, location=""):
    if isinstance(raw, list):
        for i, value in enumerate(raw):
            check_references(value, refs, path, errors, f"{location}.{i}")
    if not isinstance(raw, dict):
        return
    for key, value in raw.items():
        kind = {"owner": "person", "author": "person", "by": "person", "person": "person",
                "attendees": "person", "goal": "goal", "customer": "customer"}.get(key)
        if kind and not isinstance(value, (dict, list)) and value is not None:
            if value not in refs[kind]:
                errors.setdefault(str(path), []).append(f"{location}.{key}: unknown {key} {value!r}")
        elif key == "attendees" and isinstance(value, list):
            for person in value:
                if person not in refs["person"]:
                    errors.setdefault(str(path), []).append(f"{location}: unknown attendee {person!r}")
        check_references(value, refs, path, errors, f"{location}.{key}")

def validate_inputs(root):
    root, errors = Path(root), {}
    raw_p = load_raw(root / "purpose.json", errors)
    raw_h = load_raw(root / "history.json", errors)
    purpose = parse(Purpose, raw_p, root / "purpose.json", errors)
    history = parse(History, raw_h, root / "history.json", errors)
    refs = {}
    for kind, raw, key, path in (("person", raw_h, "people", root / "history.json"),
                               ("customer", raw_h, "customers", root / "history.json"),
                               ("goal", raw_p, "goals", root / "purpose.json"),
                               ("ticket", raw_h, "tickets", root / "history.json")):
        ids = [v.get("id") for v in raw.get(key, []) if isinstance(v, dict)] if isinstance(raw, dict) else []
        refs[kind] = set(ids)
        if len(ids) != len(set(ids)):
            errors.setdefault(str(path), []).append(f"duplicate {kind} IDs")
    if purpose and len({g.rank for g in purpose.goals}) != len(purpose.goals):
        errors.setdefault(str(root / "purpose.json"), []).append("goal ranks must be unique")
    check_references(raw_h, refs, root / "history.json", errors)
    if history:
        for entry in history.oncall:
            if entry.week_of.weekday() != 0:
                errors.setdefault(str(root / "history.json"), []).append(f"oncall {entry.week_of} is not a Monday")
    tickets = {}
    for raw in raw_h.get("tickets", []) if isinstance(raw_h, dict) else []:
        ticket = parse(Ticket, raw, root / "history.json", errors)
        if ticket:
            tickets[ticket.id] = ticket
    paths = sorted((root / "days").glob("*.json"))
    numbered = {}
    for path in paths:
        match = re.fullmatch(r"day_([0-9]{2,})\.json", path.name)
        if not match or path.name != f"day_{int(match[1]):02}.json":
            errors.setdefault(str(path), []).append("expected day_NN.json")
        else:
            numbered[int(match[1])] = path
    if not numbered or sorted(numbered) != list(range(1, max(numbered, default=0) + 1)):
        errors.setdefault(str(root / "days"), []).append("days must start at 01 with no gaps")
    days, states = [], {}
    previous = history.as_of if history else None
    for number, path in sorted(numbered.items()):
        raw = load_raw(path, errors)
        day = parse(Day, raw, path, errors)
        check_references(raw, refs, path, errors)
        if day:
            days.append(day)
            if day.day != number:
                errors.setdefault(str(path), []).append(f"day must equal file number {number}")
            if previous and day.date <= previous:
                errors.setdefault(str(path), []).append("date must be later than previous day/history")
            previous = day.date
        for change in raw.get("jira", []) if isinstance(raw, dict) else []:
            change = parse(JiraChange, change, path, errors)
            if change:
                try:
                    apply_change(tickets, change, number)
                except ValueError as exc:
                    errors.setdefault(str(path), []).append(str(exc))
        states[number] = {key: value.model_copy(deep=True) for key, value in tickets.items()}
    return purpose, history, days, states, errors
