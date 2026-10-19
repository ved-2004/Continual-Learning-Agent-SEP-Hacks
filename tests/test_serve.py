import json, threading, time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import pytest
from unittest.mock import MagicMock
from agent import evaluate, llm, render, store
from agent.schemas import Brain, LearnOutput
from agent.serve import AUTH_ERROR, IP_ERROR, KEY_ERROR, LIMIT_ERROR, server
from test_leakage import DATA, stub_plan

@pytest.fixture
def live(tmp_path, monkeypatch, request):
    limit = getattr(request, "param", "1")
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
    for key, value in (("TEACH_LIMIT", limit), ("LLM_PROVIDER", "anthropic"), ("LLM_MODEL", "test"), ("CACHE_DIR", ".cache")):
        monkeypatch.setenv(key, value)
    (tmp_path / "prompts").symlink_to(DATA.parent / "prompts", target_is_directory=True)
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui/index.html").write_text("supplied UI")
    (tmp_path / ".env").write_text("private sentinel")
    (tmp_path / "ui/escape").symlink_to(tmp_path / ".env")
    record = next(r for r in evals[1].records if r.person == "nina")
    request = dict(day=1, person="nina", final=record.final, reason=record.reason)
    expected = render.learn_message(history, days[0], [], feedback, states[1])
    calls, messages, original, clients = [], [], llm.call, []
    output = LearnOutput(ops=[dict(op="add", rule="Prioritize blockers", applies_to=["all"], from_people=["nina"])])
    def client(**kwargs):
        clients.append(kwargs)
        sdk = MagicMock()
        def send(**body):
            reply = output if "CORRECTIONS" in body["messages"][0]["content"] else next_plan
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=reply.model_dump_json())])
        sdk.messages.create.side_effect = send
        return sdk
    monkeypatch.setattr(llm.anthropic, "Anthropic", client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server-key-must-not-be-used")
    def canned(step, day, system, user, out, check):
        calls.append(step)
        messages.append(user)
        return original(step, day, system, user, out, check)
    monkeypatch.setattr(llm, "call", canned)
    httpd = server(SimpleNamespace(data=DATA, run="learn", port=0))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_port}"
    def post(value, key=None, ip=None):
        headers = {"Content-Type": "application/json"}
        if key is not None: headers["X-LLM-Key"] = key
        if ip is not None: headers["X-Forwarded-For"] = ip
        try:
            response = urlopen(Request(url + "/api/teach", data=json.dumps(value).encode(), headers=headers), timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.code, json.load(response)
    try:
        yield SimpleNamespace(**locals())
    finally:
        httpd.shutdown()
        thread.join()
        httpd.server_close()

def test_teach(live, monkeypatch, capsys):
    post, request, url = live.post, live.request, live.url
    root, feedback, next_plan, days, states = live.root, live.feedback, live.next_plan, live.days, live.states
    key = "visitor-test-key"
    try:
        assert json.load(urlopen(url + "/api/health"))["days"] == len(days)
        assert urlopen(url).read() == b"supplied UI"
        for path in ("/.env", "/../.env", "/%2e%2e/.env", "/agent/llm.py", "/escape"):
            with pytest.raises(HTTPError) as error:
                urlopen(url + path)
            assert error.value.code == 404
        code, result = post(request, key)
        assert code == 200 and set(result) == {"day", "next_day", "ops", "lessons", "changed", "plan", "baseline", "calls", "cache_hits", "seconds"}
        assert result["changed"] == ["L1"] and result["calls"] == 2 and result["cache_hits"] == 0
        assert result["baseline"] == result["plan"] == next_plan.model_dump(mode="json")
        assert store.read_json(root / "day_01/feedback.json") == [f.model_dump(mode="json") for f in feedback]
        assert live.calls == ["learn", "decide"]
        assert live.messages[0] == live.expected  # Other people's saved edits and order are unchanged.
        assert all(c["api_key"] == key for c in live.clients)
        code, result = post(request)
        assert code == 200 and result["cache_hits"] == result["calls"] == 2
        changed = dict(request, reason="New correction")
        assert post(changed, key) == (429, {"error": LIMIT_ERROR})
        tomorrow = time.time() + 86400
        monkeypatch.setattr(time, "time", lambda: tomorrow)
        assert post(changed, key)[0] == 200
        done = next(t.id for t in states[2].values() if t.status == "done")
        assert post(dict(request, day=2, final=[done, "TW-103", "TW-105"]))[0] == 400
        for value in ([], dict(request, day=True), dict(request, person=[]), dict(request, final=[{}, "x", "y"]), dict(request, reason="")):
            assert post(value)[0] == 400
        tomorrow += 86400
        def failed(*args):
            print("private sentinel")
            raise RuntimeError("private sentinel")
        monkeypatch.setattr(llm, "request", failed)
        assert post(dict(request, reason="Another correction"), key) == (500, {"error": "Teach failed; check the saved run for details."})
        assert "private sentinel" not in (llm._run_dir / "llm_calls.jsonl").read_text() + capsys.readouterr().out
    finally:
        assert llm.visitor_key.get() is None

def test_visitor_keys(live, monkeypatch, capsys):
    post, request, key = live.post, live.request, "visitor-only-secret"
    original, contexts = llm.request, []
    def provider(*args):
        assert llm.visitor_key.get() == key
        print(key)
        return original(*args).replace("Prioritize blockers", f"Prioritize blockers {key}")
    monkeypatch.setattr(llm, "request", provider)
    reset = llm.visitor_key.reset
    class ContextProbe:
        def get(self): return live_context.get()
        def set(self, value): return live_context.set(value)
        def reset(self, token):
            reset(token)
            contexts.append(live_context.get())
    live_context = llm.visitor_key
    monkeypatch.setattr(llm, "visitor_key", ContextProbe())
    assert post(request) == (401, {"error": KEY_ERROR})
    assert not live.clients
    code, result = post(request, key)
    assert code == 200 and result["calls"] == 2
    assert [c["api_key"] for c in live.clients] == [key, key]
    assert post(request)[1]["cache_hits"] == 2
    assert post(dict(request, reason="Uncached correction")) == (401, {"error": KEY_ERROR})
    assert contexts == [None] * 4 and len(live.clients) == 2
    assert key not in json.dumps(result) + str(capsys.readouterr())
    assert all(key not in p.read_text() and key not in p.name for folder in ("runs/live", ".cache") for p in live.tmp_path.joinpath(folder).rglob("*") if p.is_file())

def test_auth_error(live, monkeypatch, capsys):
    key = "rejected-visitor-secret"
    def rejected(**kwargs):
        raise llm.anthropic.AuthenticationError(key, response=MagicMock(status_code=401, headers={}), body={"error": key})
    monkeypatch.setattr(llm.anthropic, "Anthropic", rejected)
    assert live.post(live.request, key) == (401, {"error": AUTH_ERROR})
    assert key not in (llm._run_dir / "llm_calls.jsonl").read_text() + str(capsys.readouterr())
    assert not list(live.tmp_path.glob(".cache/*.txt"))

@pytest.mark.parametrize("live", ["40"], indirect=True)
def test_ip_limit(live, monkeypatch):
    for n in range(10):
        assert live.post(dict(live.request, reason=f"Correction {n}"), "visitor", "198.51.100.1, 10.0.0.1")[0] == 200
    changed = dict(live.request, reason="Eleventh correction")
    assert live.post(changed, "visitor", "198.51.100.1") == (429, {"error": IP_ERROR})
    assert live.post(dict(live.request, reason="Correction 0"), ip="198.51.100.1")[1]["cache_hits"] == 2
    assert live.post(changed, "visitor", "198.51.100.2")[0] == 200
    later = time.time() + 3601
    monkeypatch.setattr(time, "time", lambda: later)
    assert live.post(dict(changed, reason="After the hour"), "visitor", "198.51.100.1")[0] == 200
