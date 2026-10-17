from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import date
import pytest
from agent.evaluate import score_day, score_person, simulate_feedback
from agent.schemas import EvalDay, Plan


@pytest.mark.parametrize("proposed,expected", [(["A", "B", "C"], 100), (["D", "E", "F"], 0),
    (["B", "A", "C"], 58.3), (["C", "A", "B"], 50), (["A", "X", "Y"], 50), (["X", "Y", "A"], 25)])
def test_scores(proposed, expected):
    assert score_person(proposed, ["A", "B", "C"]) == expected


def test_feedback():
    plan = Plan(plans=[dict(person="nina", top3=[dict(ticket=f"TW-{i}", hours=1, why="Work", sources=[f"TW-{i}"])
                                              for i in (1, 2, 3)], lessons_used=[], note="Focus")])
    ev = EvalDay(day=1, records=[dict(person="nina", final=["TW-1", "TW-2", "TW-3"], reason="Reason", hidden_fact="Secret")])
    assert simulate_feedback(plan, ev)[0].action == "accept"
    assert simulate_feedback(plan, ev)[0].reason is None
    ev.records[0].final.reverse()
    feedback = simulate_feedback(plan, ev)[0]
    assert feedback.action == "edit" and feedback.reason == "Reason"
    assert "hidden_fact" not in feedback.model_dump()
    assert score_day(plan, ev, date(2026, 10, 19)).mean == 66.7
