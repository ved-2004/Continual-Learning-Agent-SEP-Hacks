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


def test_guard_cache_retry(tmp_path, monkeypatch):
    import json
    import pytest
    from agent import llm
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "test")
    llm.start_run(tmp_path / "run")
    replies, calls = iter(['{"summary":"missing items"}', '```json\n{"summary":"ok","items":[]}\n```']), []
    def request(*args):
        calls.append(args)
        return next(replies)
    monkeypatch.setattr(llm, "request", request)
    llm.set_forbidden([])
    for _ in range(2):
        assert llm.call("interpret", 1, "system", "a hidden fact that is twenty characters long", Brain, lambda b: []).summary == "ok"
    assert len(calls) == 2 and "failed these checks" in calls[1][-1]
    logs = [json.loads(line) for line in (tmp_path / "run/llm_calls.jsonl").read_text().splitlines()]
    assert [line["cache_hit"] for line in logs] == [False, False, True, True]
    llm.set_forbidden(["system", "user", "a hidden fact that is twenty characters long"])
    with pytest.raises(RuntimeError, match="interpret day 1: forbidden"):
        llm.call("interpret", 1, "system", "a hidden fact that is twenty characters long", Brain, lambda b: [])
    assert len(calls) == 2
    llm.set_forbidden([])
    monkeypatch.setattr(llm, "request", lambda *args: '{"summary":"ok","items":[]}')
    with pytest.raises(RuntimeError, match="decide day 2"):
        llm.call("decide", 2, "system", "bad", Brain, lambda b: ["failed check"])
    assert (tmp_path / "run/failed_decide_day02.txt").exists()


def test_sdk_request(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import patch
    from agent import llm
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-test")
    client = llm.anthropic.Anthropic(api_key="offline-test")
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="{}")])
    with patch.object(llm.anthropic, "Anthropic", return_value=client), patch.object(client.messages, "_post", return_value=response) as post:
        assert llm.request("anthropic", "test", "system", "user") == "{}"
        assert post.call_args.kwargs["body"]["max_tokens"] == 4096
        assert post.call_args.kwargs["options"]["extra_json"] == {"temperature": 0}


def test_output_checks():
    from agent import steps
    _, history, days, states, _ = store.validate_inputs(DATA)
    people, tickets = {p.id for p in history.people}, states[1]
    plan = stub_plan(history, tickets)
    assert steps.check_plan(plan, people, tickets, set(), []) == []
    plan.plans[0].top3[0].hours = 8
    plan.plans[0].lessons_used = ["L99"]
    plan.plans[0].top3[1].sources = ["D99-S1"]
    assert len(steps.check_plan(plan, people, tickets, set(), [])) == 3
    brain = Brain(summary="ok", items=[dict(type="status", text="Work", person="ghost", ticket=None, sources=["D99-S1"])])
    assert len(steps.check_brain(brain, people, tickets, set())) == 2
