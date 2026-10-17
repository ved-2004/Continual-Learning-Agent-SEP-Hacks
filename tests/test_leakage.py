from pathlib import Path
from agent import evaluate, render, store
from agent.schemas import Brain, Lesson, Plan

DATA = Path(__file__).resolve().parents[1] / "data"


def stub_plan(history, tickets):
    return Plan(plans=[dict(person=p.id, top3=[dict(ticket=t.id, hours=1, why="Work", sources=[t.id]) for t in
        [t for t in tickets.values() if t.owner == p.id and t.status != "done"][:3]], lessons_used=[], note="Focus") for p in history.people])


def test_messages():
    purpose, history, days, states, errors = store.validate_inputs(DATA)
    evals, facts = evaluate.load_evals(DATA, history, states, errors)
    assert not errors
    brain, previous = Brain(summary="", items=[]), None
    lessons = [Lesson(id="L1", rule="When work is blocked, resolve blockers.", applies_to=["all"], created_day=1, updated_day=1, evidence=["D1:nina"])]
    for day in days[:2]:
        tickets = states[day.day]
        plan = stub_plan(history, tickets)
        feedback = evaluate.simulate_feedback(plan, evals[day.day])
        messages = [render.interpret_message(purpose, history, day, brain if previous else None, tickets),
                    render.decide_message(purpose, history, day, brain, tickets, lessons, previous),
                    render.learn_message(history, day, lessons, feedback, tickets)]
        assert all(fact not in message for fact in facts for message in messages)
        assert all(f.reason not in messages[0] and f.reason not in messages[1] for f in feedback if f.action == "edit")
        assert "RECENT HISTORY" in messages[0] if day.day == 1 else "RECENT HISTORY" not in messages[0]
        if day.day == 2:
            print(messages[1])
        previous = plan
