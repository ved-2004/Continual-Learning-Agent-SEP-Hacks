import json
from agent import monitor, store


def test_health(tmp_path, capsys):
    calls = [dict(day=2, step="decide", attempt=1, errors=["TW-140 is not open"]), dict(day=2, step="decide", attempt=2, errors=[])]
    (tmp_path / "llm_calls.jsonl").write_text("\n".join(json.dumps(c) for c in calls))
    for day, mean in ((1, 60), (2, 45)):
        for name, value in (("score", dict(mean=mean, people=dict(nina=mean - 5, sam=mean + 5))),
                            ("lessons", [dict(id="L1")]),
                            ("plan", dict(plans=[dict(person=p, lessons_used=["L1"]) for p in ("nina", "sam")])),
                            ("feedback", [dict(person="nina", action="edit"), dict(person="sam", action="accept")])):
            store.write_json(tmp_path / f"day_{day:02}/{name}.json", value)
        monitor.health(tmp_path, day)
    alerts = store.read_json(tmp_path / "day_02/health.json")["alerts"]
    assert {a["kind"] for a in alerts} == {"repaired", "rejected_plan", "regression", "lesson_overridden"}
    assert next(a for a in alerts if a["kind"] == "lesson_overridden")["lesson"] == "L1"
    assert next(a for a in alerts if a["kind"] == "rejected_plan")["person"] == "nina"
    assert store.read_json(tmp_path / "lesson_health.json") == {"L1": dict(uses=4, overrides=2, flagged=True)}
    assert capsys.readouterr().out.count("lesson L1 may be stale or wrong") == 1
