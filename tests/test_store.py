from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shutil
import pytest
from agent.cli import validate
from agent.schemas import JiraChange
from agent.store import apply_change, read_json, source_items, validate_inputs, write_json

DATA = Path(__file__).resolve().parents[1] / "data"


def test_changes():
    tickets = {}
    def change(action="update", ticket="TW-1", comment=None, **patch):
        return apply_change(tickets, JiraChange(ticket=ticket, action=action, by="nina", set=patch, comment=comment), 1)
    change("create", title="Work", owner="nina", status="todo", priority="high", due="2026-10-20")
    change(status="in_progress")
    assert tickets["TW-1"].due.isoformat() == "2026-10-20"
    change(due=None)
    assert tickets["TW-1"].due is None
    change(comment="Ready")
    assert tickets["TW-1"].comments[0].text == "Ready"
    for kwargs in (dict(action="create"), dict(ticket="TW-2"), {}, dict(owner=None)):
        with pytest.raises(ValueError):
            change(**kwargs)
    assert tickets["TW-1"].owner == "nina"


def test_real_data_and_sources(capsys):
    _, _, days, _, _, _ = validate(DATA)
    history = read_json(DATA / "history.json")
    files = list((DATA / "days").glob("day_*.json"))
    tickets = {t["id"] for t in history["tickets"]}
    tickets.update(c["ticket"] for file in files for c in read_json(file)["jira"] if c["action"] == "create")
    assert capsys.readouterr().out.strip() == f"OK: {len(files)} days, {len(tickets)} tickets, {len(history['people'])} people"
    ids = [key for key, _ in source_items(days[0])]
    assert ids == ["D1-M1"] + [f"D1-S{i}" for i in range(1, 10)] + [f"D1-J{i}" for i in range(1, 7)]


@pytest.mark.parametrize("extra_file", ["history.json", "eval/day_02.json"])
def test_all_errors(tmp_path, capsys, extra_file):
    root = tmp_path / "data"
    shutil.copytree(DATA, root)
    path = root / "history.json"
    raw = read_json(path)
    raw["tickets"][0]["owner"] = "ghost"
    if extra_file == "history.json":
        raw["typo"] = True
    write_json(path, raw)
    path = root / "eval/day_02.json"
    raw = validate(DATA)[4][2].model_dump(mode="json")
    raw["records"][0]["final"][0] = "TW-101"
    if extra_file == "eval/day_02.json":
        raw["typo"] = True
    write_json(path, raw)
    with pytest.raises(SystemExit):
        validate(root)
    output = capsys.readouterr().out
    assert "typo" in output and "unknown owner 'ghost'" in output and "TW-101 missing or done" in output
