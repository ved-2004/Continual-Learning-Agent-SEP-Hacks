import json, threading, time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import pytest
from agent import evaluate, llm, render, store
from agent.schemas import Brain, LearnOutput
from agent.serve import LIMIT_ERROR, server
from test_leakage import DATA, stub_plan

def test_teach(tmp_path, monkeypatch, capsys):
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
    for key, value in (("TEACH_LIMIT", "1"), ("LLM_PROVIDER", "anthropic"), ("LLM_MODEL", "test"), ("CACHE_DIR", ".cache")):
        monkeypatch.setenv(key, value)
    (tmp_path / "prompts").symlink_to(DATA.parent / "prompts", target_is_directory=True)
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui/index.html").write_text("supplied UI")
    (tmp_path / ".env").write_text("private sentinel")
    (tmp_path / "ui/escape").symlink_to(tmp_path / ".env")
    record = next(r for r in evals[1].records if r.person == "nina")
    request = dict(day=1, person="nina", final=record.final, reason=record.reason)
    expected = render.learn_message(history, days[0], [], feedback, states[1])
    calls, original = [], llm.call
    output = LearnOutput(ops=[dict(op="add", rule="Prioritize blockers", applies_to=["all"], from_people=["nina"])])
    monkeypatch.setattr(llm, "request", lambda provider, model, system, user: (output if "CORRECTIONS" in user else next_plan).model_dump_json())
    def canned(step, day, system, user, out, check):
        calls.append(step)
        if len(calls) == 1:
            assert user == expected  # Everyone else's saved edits remain in the original order.
        return original(step, day, system, user, out, check)
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
        for path in ("/.env", "/../.env", "/%2e%2e/.env", "/agent/llm.py", "/escape"):
            with pytest.raises(HTTPError) as error:
                urlopen(url + path)
            assert error.value.code == 404
        code, result = post(request)
        assert code == 200 and set(result) == {"day", "next_day", "ops", "lessons", "changed", "plan", "baseline", "calls", "cache_hits", "seconds"}
        assert result["changed"] == ["L1"] and result["calls"] == 2 and result["cache_hits"] == 0
        assert result["baseline"] == result["plan"] == next_plan.model_dump(mode="json")
        assert store.read_json(root / "day_01/feedback.json") == [f.model_dump(mode="json") for f in feedback]
        assert calls == ["learn", "decide"]
        code, result = post(request)
        assert code == 200 and result["cache_hits"] == result["calls"] == 2
        changed = dict(request, reason="New correction")
        assert post(changed) == (429, {"error": LIMIT_ERROR})
        tomorrow = time.time() + 86400
        monkeypatch.setattr(time, "time", lambda: tomorrow)
        assert post(changed)[0] == 200
        done = next(t.id for t in states[2].values() if t.status == "done")
        assert post(dict(request, day=2, final=[done, "TW-103", "TW-105"]))[0] == 400
        for value in ([], dict(request, day=True), dict(request, person=[]), dict(request, final=[{}, "x", "y"]), dict(request, reason="")):
            assert post(value)[0] == 400
        tomorrow += 86400
        def failed(*args):
            print("private sentinel")
            raise RuntimeError("private sentinel")
        monkeypatch.setattr(llm, "request", failed)
        assert post(dict(request, reason="Another correction")) == (500, {"error": "Teach failed; check the saved run for details."})
        assert "private sentinel" not in (llm._run_dir / "llm_calls.jsonl").read_text() + capsys.readouterr().out
    finally:
        httpd.shutdown()
        thread.join()
        httpd.server_close()
