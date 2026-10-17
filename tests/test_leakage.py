from pathlib import Path
import sys, json
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import evaluate, render, store, llm, steps
from agent.schemas import Brain, Lesson, Plan, LearnOutput

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
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setenv("CACHE_DIR", "custom/cache")
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
    assert len(list(Path("custom/cache").glob("*.txt"))) == 2
    logs = [json.loads(line) for line in (tmp_path / "run/llm_calls.jsonl").read_text().splitlines()]
    assert [line["cache_hit"] for line in logs] == [False, False, True, True]
    llm.set_forbidden(["system", "user", "a hidden fact that is twenty characters long"])
    with pytest.raises(RuntimeError, match="interpret day 1: forbidden"):
        llm.call("interpret", 1, "system", "a hidden fact that is twenty characters long", Brain, lambda b: [])
    assert len(calls) == 2
    llm.set_forbidden([])
    monkeypatch.setattr(llm, "request", lambda *args: '{"summary":"ok","items":[]}')
    with pytest.raises(RuntimeError, match="decide day 2"):
        llm.call("decide", 2, "system", "bad", Brain, lambda b: ["failed check" + "x" * 300])
    assert (tmp_path / "run/failed_decide_day02.txt").exists()
    assert len(json.loads((tmp_path / "run/llm_calls.jsonl").read_text().splitlines()[-1])["errors"][0]) == 200

@pytest.mark.parametrize("provider,stop", [("anthropic", "end_turn"), ("anthropic", "max_tokens"), ("openai", "stop"), ("openai", "length")])
def test_sdk_request(tmp_path, monkeypatch, provider, stop):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    monkeypatch.chdir(tmp_path)
    for key, value in (("LLM_PROVIDER", provider), ("LLM_MODEL", "test"), (f"{provider.upper()}_API_KEY", "offline-test")):
        monkeypatch.setenv(key, value)
    llm.start_run(tmp_path / "run")
    llm.set_forbidden([])
    raw = '{"summary":"' + "x" * 5000 + '","items":[]}'
    response = (SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=raw)]) if provider == "anthropic"
                else SimpleNamespace(choices=[SimpleNamespace(finish_reason=stop, message=SimpleNamespace(content=raw))]))
    client = MagicMock()
    send = client.messages.create if provider == "anthropic" else client.chat.completions.create
    send.return_value = response
    monkeypatch.setattr(llm.anthropic if provider == "anthropic" else llm.openai,
                        "Anthropic" if provider == "anthropic" else "OpenAI", lambda **kwargs: client)
    if stop in ("max_tokens", "length"):
        with pytest.raises(RuntimeError, match="output truncated"):
            llm.call("decide", 3, "system", "user", Brain, lambda b: [])
        assert not list(Path(".cache").glob("*.txt"))
    else:
        assert llm.call("decide", 3, "system", "user", Brain, lambda b: []).summary == "x" * 5000
        assert next(Path(".cache").glob("*.txt")).read_text() == raw
    assert send.call_count == 1
    kwargs = send.call_args.kwargs
    assert kwargs["max_tokens" if provider == "anthropic" else "max_completion_tokens"] == 16000
    assert (kwargs["extra_body"] if provider == "anthropic" else kwargs)["temperature"] == 0
    assert "thinking" not in kwargs and "reasoning_effort" not in kwargs

def test_output_checks():
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

def test_lesson_lifecycle():
    output = LearnOutput(ops=[dict(op="add", rule="Do it", applies_to=["all"], from_people=["nina"])])
    assert steps.check_learning(output, [], {"nina"}, {"nina"}) == []
    assert steps.check_learning(output, [], {"nina"}, set())
    lessons = steps.apply_ops([], output, 1)
    output.ops[0].op, output.ops[0].id = "update", "L1"
    updated = steps.apply_ops(lessons, output, 2)
    assert updated[0].evidence == ["D1:nina", "D2:nina"] and updated[0].created_day == 1
    assert updated[0].updated_day == 2 and lessons[0].updated_day == 1
