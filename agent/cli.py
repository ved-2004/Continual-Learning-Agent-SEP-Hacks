import argparse
from pathlib import Path
from agent import evaluate, store


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


def main():
    parser = argparse.ArgumentParser(description="Tidewell daily priority agent")
    parser.add_argument("--data", type=Path, default=Path("data"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    args = parser.parse_args()
    validate(args.data)
