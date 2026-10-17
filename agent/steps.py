from pathlib import Path
from agent import llm, render
from agent.schemas import Brain, Plan


def valid_sources(sources, known):
    return 1 <= len(sources) <= 5 and set(sources) <= known


def check_brain(brain, people, tickets, sources):
    errors = []
    if len(brain.items) > 25 or len(brain.summary.split()) > 90:
        errors.append("Brain: at most 25 items and 90 summary words")
    for i, item in enumerate(brain.items, 1):
        if len(item.text.split()) > 45 or (item.person is not None and item.person not in people) or (item.ticket is not None and item.ticket not in tickets):
            errors.append(f"Brain item {i}: text exceeds 45 words or person/ticket is unknown")
        if not valid_sources(item.sources, sources):
            errors.append(f"Brain item {i}: expected 1-5 known source IDs")
    return errors


def check_plan(plan, people, tickets, sources, lessons):
    errors, ids = [], [p.person for p in plan.plans]
    if len(ids) != len(people) or set(ids) != people:
        errors.append("Plan must contain exactly one entry per person")
    for p in plan.plans:
        if len(p.top3) != 3 or len({i.ticket for i in p.top3}) != 3:
            errors.append(f"{p.person}: expected exactly 3 distinct tickets")
        if sum(i.hours for i in p.top3) > 8 or any(not 0.5 <= i.hours <= 8 for i in p.top3):
            errors.append(f"{p.person}: hours must be 0.5-8 each and total at most 8")
        if len(p.note.split()) > 60 or not set(p.lessons_used) <= {l.id for l in lessons}:
            errors.append(f"{p.person}: note exceeds 60 words or lesson ID was not shown")
        for i in p.top3:
            if i.ticket not in tickets or tickets[i.ticket].status == "done" or len(i.why.split()) > 40:
                errors.append(f"{p.person}/{i.ticket}: ticket missing/done or why exceeds 40 words")
            if not valid_sources(i.sources, sources | set(tickets)):
                errors.append(f"{p.person}/{i.ticket}: expected 1-5 known sources/tickets")
    return errors


def interpret(purpose, history, day, brain, tickets, sources):
    return llm.call("interpret", day.day, Path("prompts/interpret.md").read_text(),
        render.interpret_message(purpose, history, day, brain, tickets), Brain,
        lambda b: check_brain(b, {p.id for p in history.people}, tickets, sources))


def decide(purpose, history, day, brain, tickets, sources, lessons, previous):
    plan = llm.call("decide", day.day, Path("prompts/decide.md").read_text(),
        render.decide_message(purpose, history, day, brain, tickets, lessons, previous), Plan,
        lambda p: check_plan(p, {p.id for p in history.people}, tickets, sources, lessons))
    plan.plans.sort(key=lambda p: [person.id for person in history.people].index(p.person))
    return plan
