import argparse
import csv
import os
import re
import shutil
import sys
from pathlib import Path
from agent import evaluate, store, llm, steps


def validate(root):
    purpose, history, days, states, errors = store.validate_inputs(root)
    evals, facts = evaluate.load_evals(root, history, states, errors)
    if errors:
        for file, messages in errors.items():
            print(f"{file}:")
            for message in messages:
                print(f"  - {message}")
        raise SystemExit(1)
    print(f"OK: {len(days)} days, {len(states[max(states)])} tickets, {len(history.people)} people")
    return purpose, history, days, states, evals, facts


def run_path(name):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("Run name must contain only letters, digits, underscores or hyphens")
    path = Path("runs") / name
    if path.is_symlink():
        raise ValueError("Run directory must not be a symlink")
    return path


def run(args):
    purpose, history, days, states, evals, facts = validate(args.data)
    if args.days is not None and not 1 <= args.days <= len(days):
        raise ValueError(f"--days must be between 1 and {len(days)}")
    path = run_path(args.name or args.mode)
    if path.exists():
        shutil.rmtree(path)
    llm.start_run(path)
    llm.set_forbidden(facts)
    brain, previous, lessons, sources = None, None, [], set()
    with (path / "scores.csv").open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["day", "date", "mean", "accepts"] + [p.id for p in history.people])
        for day in days[:args.days]:
            tickets = states[day.day]
            sources.update(key for key, _ in store.source_items(day))
            brain = steps.interpret(purpose, history, day, brain, tickets, sources)
            plan = steps.decide(purpose, history, day, brain, tickets, sources, lessons, previous)
            feedback = evaluate.simulate_feedback(plan, evals[day.day])
            score = evaluate.score_day(plan, evals[day.day], day.date)
            edits = [f for f in feedback if f.action == "edit"]
            if args.mode == "learn" and edits:
                lessons = steps.apply_ops(lessons, steps.learn(history, day, lessons, edits, tickets), day.day)
            for name, value in (("brain", brain), ("plan", plan), ("feedback", feedback), ("score", score), ("lessons", lessons)):
                store.write_json(path / f"day_{day.day:02}" / f"{name}.json", value)
            writer.writerow([score.day, score.date, score.mean, score.accepts] + [score.people[p.id] for p in history.people])
            file.flush()
            print(f"day {day.day}  {day.date}  mean {score.mean:.1f}  accepts {score.accepts}/6  lessons {len(lessons)}", flush=True)
            previous = plan


def show(args):
    path = run_path(args.run)
    _, _, _, states, errors = store.validate_inputs(args.data)
    if errors or args.day not in states:
        raise ValueError(f"Cannot reconstruct ticket state: {errors or 'unknown day'}")
    day_path = path / f"day_{args.day:02}"
    plan, feedback, score = [store.read_json(day_path / f"{name}.json") for name in ("plan", "feedback", "score")]
    lessons = {l["id"]: l["rule"] for l in store.read_json(path / f"day_{args.day - 1:02}" / "lessons.json")} if args.day > 1 else {}
    selected = [p for p in plan["plans"] if args.person is None or p["person"] == args.person]
    if not selected:
        raise ValueError(f"Unknown person: {args.person}")
    print(f"{args.run} — day {args.day}, {score['date']}")
    for p in selected:
        print(f"\n{p['person'].upper()}")
        for i, item in enumerate(p["top3"], 1):
            print(f"  {i}. {item['ticket']} {states[args.day][item['ticket']].title} — {item['hours']:g}h\n     {item['why']}\n     Sources: {', '.join(item['sources'])}")
        print("  Lessons used: " + ("; ".join(f"{key}: {lessons[key]}" for key in p["lessons_used"]) or "none"))
        print(f"  Note: {p['note']}")
        f = next(f for f in feedback if f["person"] == p["person"])
        print(f"  Feedback: {f['action']}" + (f" → {', '.join(f['final'])}\n  Reason: {f['reason']}" if f["action"] == "edit" else ""))
        print(f"  Score: {score['people'][p['person']]:.1f}/100")


def report(args):
    paths = [run_path(name) for name in (args.run_a, args.run_b)]
    rows = [list(csv.DictReader((path / "scores.csv").read_text().splitlines())) for path in paths]
    if not rows[0] or [(r["day"], r["date"]) for r in rows[0]] != [(r["day"], r["date"]) for r in rows[1]]:
        raise ValueError("Reports require nonempty runs covering the same days and dates")
    print(f"day  date        {args.run_a:>8}  {args.run_b:>8}     gap  accepts A/B")
    for a, b in zip(*rows):
        print(f"{int(a['day']):3}  {a['date']}  {float(a['mean']):8.1f}  {float(b['mean']):8.1f}  {float(b['mean']) - float(a['mean']):+6.1f}  {a['accepts']}/6 {b['accepts']}/6")
    if store.read_json(paths[0] / "day_01/plan.json") != store.read_json(paths[1] / "day_01/plan.json"):
        print("Warning: day 1 plans differ; something other than lessons changed between these runs.")
    os.environ.setdefault("MPLCONFIGDIR", str(Path(".cache/matplotlib").resolve()))
    os.environ.setdefault("XDG_CACHE_HOME", str(Path(".cache").resolve()))
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for name, data in zip((args.run_a, args.run_b), rows):
        ax.plot([int(r["day"]) for r in data], [float(r["mean"]) for r in data], marker="o", label=name)
    ax.set(xlabel="Day", ylabel="Mean score", ylim=(0, 100), title="Agreement with the team, by day")
    ax.set_xticks([int(r["day"]) for r in rows[0]])
    ax.grid(alpha=0.2)
    ax.legend()
    fig.tight_layout()
    fig.savefig("runs/compare.png", dpi=160)
    plt.close(fig)
    print("Saved runs/compare.png")


def main():
    parser = argparse.ArgumentParser(description="Tidewell daily priority agent")
    parser.add_argument("--data", type=Path, default=Path("data"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    runner = sub.add_parser("run")
    runner.add_argument("--mode", choices=["learn", "nolearn"], required=True)
    runner.add_argument("--name")
    runner.add_argument("--days", type=int)
    viewer = sub.add_parser("show")
    viewer.add_argument("run")
    viewer.add_argument("--day", type=int, required=True)
    viewer.add_argument("--person")
    reporter = sub.add_parser("report")
    reporter.add_argument("run_a")
    reporter.add_argument("run_b")
    live = sub.add_parser("serve")
    live.add_argument("--run", default="learn")
    live.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            validate(args.data)
        elif args.command == "run":
            run(args)
        elif args.command == "report":
            report(args)
        elif args.command == "serve":
            from agent.serve import server
            with server(args) as httpd:
                print(f"Serving {args.run} at http://localhost:{httpd.server_port}", flush=True)
                httpd.serve_forever()
        else:
            show(args)
    except (OSError, ValueError, RuntimeError, llm.anthropic.APIError, llm.openai.APIError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
