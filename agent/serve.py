"""Serve the supplied UI and replay one correction through Learn and Decide."""
import json, threading, time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from agent import llm, steps, store
from agent.schemas import Brain, Feedback, LearnOutput, Lesson, Plan


def server(args):
    from agent.cli import run_path, validate
    purpose, history, days, states, _, facts = validate(args.data)
    llm.set_forbidden(facts)
    root, lock = run_path(args.run), threading.Lock()
    if not root.is_dir():
        raise ValueError(f"Run does not exist: {root}")

    def saved(day, name):
        return store.read_json(root / f"day_{day:02}" / f"{name}.json")

    def validate_request(data):
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        day, person, final, reason = (data.get(k) for k in ("day", "person", "final", "reason"))
        if type(day) is not int or not 1 <= day < len(days):
            raise ValueError(f"day must be between 1 and {len(days) - 1}")
        if person not in [p.id for p in history.people]:
            raise ValueError("person must exist in history")
        if not isinstance(final, list) or len(final) != 3 or any(not isinstance(t, str) for t in final) or len(set(final)) != 3:
            raise ValueError("final must contain 3 distinct ticket IDs")
        if any(t not in states[day] or states[day][t].status == "done" for t in final):
            raise ValueError("final tickets must be open after today's Jira changes")
        plan = Plan.model_validate(saved(day, "plan"))
        proposed = [i.ticket for p in plan.plans if p.person == person for i in p.top3]
        if not isinstance(reason, str) or (final != proposed and not reason.strip()):
            raise ValueError("reason must be a string, nonempty for an edit")
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
        next_plan = steps.decide(purpose, history, days[day], Brain.model_validate(saved(day + 1, "brain")),
                                 states[day + 1], sources, lessons, plan)
        for name, value in (("ops", output.ops), ("lessons", lessons), ("plan", next_plan)):
            store.write_json(path / f"{name}.json", value)
        calls = [json.loads(line) for line in (path / "llm_calls.jsonl").read_text().splitlines()]
        return dict(day=day, next_day=day + 1, ops=output.ops, lessons=lessons,
                    changed=[l.id for l in lessons if l not in base], plan=next_plan, baseline=saved(day + 1, "plan"),
                    calls=len(calls), cache_hits=sum(c["cache_hit"] for c in calls), seconds=round(time.perf_counter() - started, 2))

    def respond(handler, status, value):
        body = json.dumps(value, default=lambda v: v.model_dump(mode="json")).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def get(handler):
        if handler.path == "/api/health":
            respond(handler, 200, dict(ok=True, run=args.run, days=len(days)))
        else:
            SimpleHTTPRequestHandler.do_GET(handler)

    def post(handler):
        if handler.path != "/api/teach":
            return respond(handler, 404, dict(error="Unknown endpoint"))
        with lock:
            try:
                size = int(handler.headers.get("Content-Length", "0"))
                if not 0 < size <= 1_000_000:
                    raise ValueError("Expected a JSON request of at most 1 MB")
                data = json.loads(handler.rfile.read(size))
                values = validate_request(data)
            except (ValueError, TypeError, KeyError, RecursionError) as exc:
                return respond(handler, 400, dict(error=str(exc)))
            except Exception as exc:
                return respond(handler, 500, dict(error=str(exc)))
            try:
                result = teach(data, *values)
            except Exception as exc:
                return respond(handler, 500, dict(error=str(exc)))
            respond(handler, 200, result)

    def handle(request, address, httpd):
        # Attach plain functions before the standard handler processes the request.
        handler = SimpleHTTPRequestHandler.__new__(SimpleHTTPRequestHandler)
        handler.do_GET, handler.do_POST = lambda: get(handler), lambda: post(handler)
        handler.__init__(request, address, httpd, directory="ui")

    return ThreadingHTTPServer(("127.0.0.1", args.port), handle)
