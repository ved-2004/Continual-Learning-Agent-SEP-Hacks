import json, threading
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from agent import evaluate, llm, render, steps, store
from agent.schemas import Brain, LearnOutput
from agent.serve import server
from test_leakage import DATA, stub_plan


def test_teach(tmp_path, monkeypatch):
    _, history, days, states, errors = store.validate_inputs(DATA)
    evals, _ = evaluate.load_evals(DATA, history, states, errors)
    plan = stub_plan(history, states[1])
    feedback = evaluate.simulate_feedback(plan, evals[1])
    root = tmp_path / "runs/learn"
    for name, value in (("plan", plan), ("feedback", feedback)):
        store.write_json(root / f"day_01/{name}.json", value)
    next_plan = stub_plan(history, states[2])
    for name, value in (("plan", next_plan), ("brain", Brain(summary="", items=[]))):
        store.write_json(root / f"day_02/{name}.json", value)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "prompts").symlink_to(DATA.parent / "prompts", target_is_directory=True)
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui/index.html").write_text("supplied UI")
    record = next(r for r in evals[1].records if r.person == "nina")
    request = dict(day=1, person="nina", final=record.final, reason=record.reason)
    expected = render.learn_message(history, days[0], [], feedback, states[1])
    calls = []
    def canned(step, day, system, user, out, check):
        calls.append(step)
        result = LearnOutput(ops=[dict(op="add", rule="Prioritize blockers", applies_to=["all"], from_people=["nina"])]) if step == "learn" else next_plan
        if step == "learn":
            assert user == expected  # Includes everyone else's saved edits in the original order.
        assert not check(result)
        with (llm._run_dir / "llm_calls.jsonl").open("a") as file:
            file.write(json.dumps(dict(cache_hit=True)) + "\n")
        return result
    monkeypatch.setattr(llm, "call", canned)
    httpd = server(SimpleNamespace(data=DATA, run="learn", port=0))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_port}"
    def post(value):
        try:
            response = urlopen(Request(url + "/api/teach", data=json.dumps(value).encode(), headers={"Content-Type": "application/json"}), timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.code, json.load(response)
    try:
        assert json.load(urlopen(url + "/api/health"))["days"] == len(days)
        assert urlopen(url).read() == b"supplied UI"
        code, result = post(request)
        assert code == 200 and set(result) == {"day", "next_day", "ops", "lessons", "changed", "plan", "baseline", "calls", "cache_hits", "seconds"}
        assert result["changed"] == ["L1"] and result["calls"] == result["cache_hits"] == 2
        assert result["baseline"] == result["plan"] == next_plan.model_dump(mode="json")
        assert store.read_json(root / "day_01/feedback.json") == [f.model_dump(mode="json") for f in feedback]
        done = next(t.id for t in states[2].values() if t.status == "done")
        assert post(dict(request, day=2, final=[done, "TW-103", "TW-105"]))[0] == 400
        for value in ([], dict(request, day=True), dict(request, person=[]), dict(request, final=[{}, "x", "y"]), dict(request, reason="")):
            assert post(value)[0] == 400
        monkeypatch.setattr(llm, "call", lambda *args: (_ for _ in ()).throw(RuntimeError("failed after retry")))
        assert post(request) == (500, {"error": "failed after retry"})
        assert calls == ["learn", "decide"]
    finally:
        httpd.shutdown()
        thread.join()
        httpd.server_close()
