"""Serve the supplied UI and replay one correction through Learn and Decide."""
import json, os, threading, time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from agent import llm, steps, store
from agent.schemas import Brain, Feedback, LearnOutput, Lesson, Plan
LIMIT_ERROR = "The live demo has reached today's limit. The Demo tab still works, or run it locally from GitHub."
KEY_ERROR = "Add your Anthropic API key to teach it something new. Replays of the demo answers work without one."
AUTH_ERROR = "That API key was rejected by the provider. Check it and try again."
IP_ERROR = "You have reached 10 new teach requests in the last hour. Try again later; demo replays still work."
def server(args):
    from agent.cli import run_path, validate
    purpose, history, days, states, _, facts = validate(args.data)
    llm.set_forbidden(facts)
    root, lock, ui = run_path(args.run), threading.Lock(), Path("ui").resolve()
    if not root.is_dir(): raise ValueError(f"Run does not exist: {root}")
    try:
        limit, port = int(os.getenv("TEACH_LIMIT", "40")), args.port if args.port is not None else int(os.getenv("PORT", "8000"))
        if limit < 0 or not 0 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError("Invalid server port or daily limit") from None
    usage, ips = [int(time.time() // 86400), 0, False], {}
    # Charge once at the first authenticated cache miss, never for replays.
    def limited(ip):
        now = time.time()
        if usage[0] != int(time.time() // 86400):
            usage[:2] = [int(time.time() // 86400), 0]
        if not usage[2]:
            for address in list(ips):
                ips[address] = [t for t in ips[address] if t > now - 3600]
                if not ips[address]: del ips[address]
            if len(ips.get(ip, [])) >= 10: raise BlockingIOError(IP_ERROR)
            if usage[1] >= limit:
                raise BlockingIOError(LIMIT_ERROR)
            ips.setdefault(ip, []).append(now)
            usage[1] += 1
            usage[2] = True
    def saved(day, name): return store.read_json(root / f"day_{day:02}" / f"{name}.json")
    def validate_request(data):
        if not isinstance(data, dict): raise ValueError("Expected a JSON object")
        day, person, final, reason = (data.get(k) for k in ("day", "person", "final", "reason"))
        if type(day) is not int or not 1 <= day < len(days): raise ValueError(f"day must be between 1 and {len(days) - 1}")
        if person not in [p.id for p in history.people]: raise ValueError("person must exist in history")
        if not isinstance(final, list) or len(final) != 3 or any(not isinstance(t, str) for t in final) or len(set(final)) != 3: raise ValueError("final must contain 3 distinct ticket IDs")
        if any(t not in states[day] or states[day][t].status == "done" for t in final): raise ValueError("final tickets must be open after today's Jira changes")
        plan = Plan.model_validate(saved(day, "plan"))
        proposed = [i.ticket for p in plan.plans if p.person == person for i in p.top3]
        if not isinstance(reason, str) or (final != proposed and not reason.strip()): raise ValueError("reason must be a string, nonempty for an edit")
        return day, person, final, reason, plan, proposed
    def teach(data, day, person, final, reason, plan, proposed):
        started = time.perf_counter()
        path = Path("runs/live") / datetime.now().strftime("%Y%m%d-%H%M%S")
        while path.exists():
            time.sleep(0.05)
            path = Path("runs/live") / datetime.now().strftime("%Y%m%d-%H%M%S")
        llm.start_run(path)
        store.write_json(path / "request.json", data)
        base = [Lesson.model_validate(l) for l in saved(day - 1, "lessons")] if day > 1 else []
        feedback = {f["person"]: Feedback.model_validate(f) for f in saved(day, "feedback")}
        feedback[person] = Feedback(person=person, action="accept" if final == proposed else "edit",
                                    proposed=proposed, final=final, reason=None if final == proposed else reason)
        edits = [feedback[p.id] for p in history.people if feedback[p.id].action == "edit"]
        output = steps.learn(history, days[day - 1], base, edits, states[day]) if edits else LearnOutput(ops=[])
        lessons = steps.apply_ops(base, output, day)
        sources = {key for d in days[:day + 1] for key, _ in store.source_items(d)}
        next_plan = steps.decide(purpose, history, days[day], Brain.model_validate(saved(day + 1, "brain")), states[day + 1], sources, lessons, plan)
        for name, value in (("ops", output.ops), ("lessons", lessons), ("plan", next_plan)):
            store.write_json(path / f"{name}.json", value)
        calls = [json.loads(line) for line in (path / "llm_calls.jsonl").read_text().splitlines()]
        return dict(day=day, next_day=day + 1, ops=output.ops, lessons=lessons, changed=[l.id for l in lessons if l not in base],
                    plan=next_plan, baseline=saved(day + 1, "plan"), calls=len(calls), cache_hits=sum(c["cache_hit"] for c in calls), seconds=round(time.perf_counter() - started, 2))
    def respond(handler, status, value):
        body = json.dumps(value, default=lambda v: v.model_dump(mode="json")).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)
    def get(handler):
        if handler.path == "/api/health":
            return respond(handler, 200, dict(ok=True, run=args.run, days=len(days)))
        try:
            path = (ui / unquote(urlsplit(handler.path).path).lstrip("/")).resolve()
            path = (path / "index.html").resolve() if path.is_dir() else path
            if not path.is_relative_to(ui) or not path.is_file():
                raise ValueError
        except (OSError, ValueError):
            return respond(handler, 404, dict(error="Not found"))
        handler.translate_path = lambda _: str(path)
        getattr(SimpleHTTPRequestHandler, "do_" + handler.command)(handler)
    def post(handler):
        if handler.path != "/api/teach":
            return respond(handler, 404, dict(error="Unknown endpoint"))
        with lock:
            ip = handler.headers.get("X-Forwarded-For", "").split(",")[0].strip() or handler.client_address[0]
            key_token = llm.visitor_key.set(handler.headers.get("X-LLM-Key", "").strip())
            limit_token, usage[2] = llm.before_request.set(lambda: limited(ip)), False
            status = 400
            try:
                size = int(handler.headers.get("Content-Length", "0"))
                if not 0 < size <= 1_000_000: raise ValueError("Expected a JSON request of at most 1 MB")
                data = json.loads(handler.rfile.read(size))
                values = validate_request(data)
                status = 500
                result = teach(data, *values)
            except llm.MissingAPIKey:
                return respond(handler, 401, dict(error=KEY_ERROR))
            except llm.RejectedAPIKey:
                return respond(handler, 401, dict(error=AUTH_ERROR))
            except BlockingIOError as exc:
                return respond(handler, 429, dict(error=str(exc)))
            except Exception:
                return respond(handler, status, dict(error="Invalid teach request" if status == 400 else "Teach failed; check the saved run for details."))
            finally:
                llm.before_request.reset(limit_token)
                llm.visitor_key.reset(key_token)
            respond(handler, 200, result)
    def handle(request, address, httpd):
        handler = SimpleHTTPRequestHandler.__new__(SimpleHTTPRequestHandler)
        handler.do_GET = handler.do_HEAD = lambda: get(handler)
        handler.do_POST, handler.log_message = lambda: post(handler), lambda *args: None
        handler.__init__(request, address, httpd, directory=str(ui))
    return ThreadingHTTPServer(("0.0.0.0" if "PORT" in os.environ else "127.0.0.1", port), handle)
