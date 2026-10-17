# Build brief: continual-learning priority agent (backend only)

Read this whole file, plus everything in `data/` and `prompts/`, before writing code. Follow it exactly. Where it conflicts with your defaults, this file wins. I'm the only engineer on a hackathon team and the demo is tomorrow afternoon, so build exactly this and nothing more.

## 1. What we are building

Tidewell is a fictional 6-person startup. Every day it produces meeting transcripts, Slack messages and Jira changes. Our agent reads them and proposes each person's top 3 tickets for the day, each with a time box and a reason. Each person accepts the list or edits it and says why. The agent turns edits into lessons and uses them on later days.

The demo is one chart: the same days run twice, once with lessons (`learn`) and once without (`nolearn`). The gap between the two lines is the proof that the agent learns. Everything you build serves that chart. There is no UI in this phase.

## 2. Done means

```
pip install -r requirements.txt
cp .env.example .env              # then add the API key
python -m agent validate
python -m agent run --mode nolearn
python -m agent run --mode learn
python -m agent report nolearn learn
python -m agent show learn --day 2 --person nina
pytest
```

All of these work. `data/` holds days 1 and 2 today. Teammates are writing days 3 to 8 in the same format, so no code may assume a fixed number of days.

## 3. Hard rules

1. **Size.** Target 800 lines of Python, hard cap 1,000, tests included (`wc -l agent/*.py tests/*.py`). Prompt files do not count. Before you pass the cap, stop and tell me what you would cut.
2. **Dependencies.** Only `requirements.txt` (pydantic, anthropic, openai, python-dotenv, matplotlib, pytest) and the standard library. Ask before adding anything.
3. **Out of scope.** No UI, web server, database, embeddings, vector store, async, threads, Docker, or agent framework (LangChain, LangGraph, LlamaIndex, CrewAI, DSPy and similar). No streaming, colored output, progress bars, or config files beyond `.env`.
4. **Structure.** Exactly the files in section 5. Pydantic models are the only classes; use built-in exceptions. Everything else is plain functions. No base classes, registries, factories, plugin hooks or `utils.py`.
5. **Read-only inputs.** Never edit anything in `data/` or `prompts/`. Teammates own the data and I own the prompts. If a prompt looks wrong, say so in your summary instead of changing it.
6. **No answer leakage.** Only `agent/evaluate.py` reads `data/eval/`. No LLM call ever contains a `hidden_fact`. A person's final list and reason reach an LLM only inside Learn's corrections, and only when that person edited, because that is what a real employee would type. Enforce this in code: section 8 and `tests/test_leakage.py`.
7. **Determinism.** Temperature 0 where the model accepts it. Every LLM call is cached on disk by a hash of its full input, so a rerun never pays for the same call twice.
8. **Validate every LLM output.** Parse it into its pydantic model, then run the checks in section 7. On any failure, retry once with the errors appended. If the retry also fails, save the raw reply and stop the run with a clear message.
9. **Fail loudly.** No silent fallbacks, no bare `except`, no default values that hide a bad input or a bad output.
10. **Development set.** Days 1 and 2 are for development. Never change code or prompts to raise scores on day 3 or later: the chart would then measure our tuning, not the agent's learning.
11. **Ambiguity.** Choose the simplest option that keeps rules 1 to 10, write it down for your final summary, and keep going. Stop only if a rule would break.

## 4. Input data

All files are JSON. Dates are `YYYY-MM-DD`, times `HH:MM`. Every input model uses `ConfigDict(extra="forbid")`, so a misspelled field is an error.

```
data/purpose.json -> Purpose
  company: str
  what_we_do: str
  mission: str
  goals: list[Goal]                 ranks unique, 1 = most important
Goal
  id: str                           "G1", "G2", "G3"
  rank: int
  goal: str
  metric: str
  due: date

data/history.json -> History        state before day 1
  as_of: date
  people: list[Person]              exactly 6
  customers: list[Customer]
  oncall: list[OnCall]
  tickets: list[Ticket]
  events: list[Event]
Person    id (lowercase first name, the key used everywhere), name, role, owns, notes (may be "")
Customer  id, name, arr_share_pct: float, renewal_date: date, health: "green" | "yellow" | "red", contact, notes
OnCall    week_of: date (a Monday), person: person id
Event     date: date, text: str
Ticket
  id: str                           "TW-" + number, unique
  title: str
  description: str                  may be ""
  owner: person id
  status: "todo" | "in_progress" | "blocked" | "done"
  priority: "high" | "medium" | "low"
  goal: goal id | None
  customer: customer id | None
  due: date | None
  estimate_hours: float | None
  (runtime only, never in files: comments: list of {day, by, text})

data/days/day_NN.json -> Day        NN = 01, 02, ...
  day: int                          equals NN
  date: date                        later than the previous day's date
  meetings: list[Meeting]
  slack: list[SlackMessage]
  jira: list[JiraChange]            applied in file order
Meeting       title, time, attendees: list[person id], transcript: list[str] (one turn per line, "Maya: text")
SlackMessage  channel: "#general" | "#eng" | "#design" | "#customers", author: person id, time, text
JiraChange
  ticket: ticket id
  action: "create" | "update"
  by: person id
  set: TicketPatch                  every Ticket field except id, all optional
  comment: str | None

data/eval/day_NN.json -> EvalDay    evaluation only, read by evaluate.py alone
  day: int
  records: list[EvalRecord]         exactly one per person
EvalRecord
  person: person id
  final: list[ticket id]            exactly 3, distinct, in order
  reason: str                       what the person says when they edit
  hidden_fact: str                  author's note, never shown to any LLM
```

Applying a JiraChange:
- `create`: the ticket ID must be new, and `set` must contain title, owner, status and priority. Missing optional fields become `""` (description) or `None`.
- `update`: the ticket must exist. Apply only the fields present in the JSON (`model_dump(exclude_unset=True)`), so an explicit `null` is applied and a missing field is left alone. An update with an empty `set` and no comment is an error.
- A non-null `comment` is appended to the ticket's comments as `{day, by, text}`.

`python -m agent validate` checks all of the following, prints every error grouped by file (not just the first), and exits with code 1 if there are any. Otherwise it prints `OK: <days> days, <tickets> tickets, 6 people`.
- Every file parses into its model.
- Person, customer, goal and ticket IDs are unique, and there are exactly 6 people.
- Every owner, author, attendee, `by`, on-call person, goal and customer reference exists.
- Day files are numbered 01, 02, ... with no gaps, `day` matches the file number, dates strictly increase, and every Jira change applies cleanly in order.
- Every day file has an eval file and the reverse. Each eval has exactly one record per person. Each `final` is 3 distinct tickets that exist and are not done after that day's Jira changes. `reason` and `hidden_fact` are not empty.
- No `hidden_fact` of 20 characters or more appears verbatim in any day file or in any `reason`.

## 5. Files to create

| File | Job | Guide (lines) |
| --- | --- | --- |
| `agent/__init__.py` | empty | 0 |
| `agent/__main__.py` | `from agent.cli import main; main()` | 2 |
| `agent/schemas.py` | every pydantic model: inputs, LLM outputs, run records | 120 |
| `agent/store.py` | load and validate inputs (not eval), assign source IDs, apply Jira changes, read and write run files | 150 |
| `agent/render.py` | turn inputs into the prompt text in the Appendix | 130 |
| `agent/llm.py` | `call()`: guard, cache, request, parse, check, retry, log | 80 |
| `agent/steps.py` | interpret, decide, learn, their checks, applying lesson ops | 150 |
| `agent/evaluate.py` | load and check eval files, simulate feedback, score | 60 |
| `agent/cli.py` | `validate`, `run`, `report`, `show` | 160 |
| `tests/test_evaluate.py`, `tests/test_store.py`, `tests/test_leakage.py` | section 12 | 120 total |

Already in the repo, do not edit: `data/`, `prompts/interpret.md`, `prompts/decide.md`, `prompts/learn.md`, `requirements.txt`, `.env.example`, `.gitignore`. Created by runs: `runs/`, `.cache/`.

## 6. The daily loop

```
context  = purpose, people, on-call, customers        (static)
tickets  = history tickets                            (code owns ticket state; no LLM edits it)
brain = None; lessons = []; prev_plan = None
for each day d, in order:
    bundle   = load day d and assign source IDs
    tickets  = apply day d's Jira changes
    brain    = interpret(context, brain, bundle, open tickets)                   # LLM, same in both modes
    plan     = decide(context, brain, open tickets, lessons if learn else [], prev_plan)   # LLM
    feedback = simulate_feedback(plan, eval day d)                               # code
    score    = score_day(plan, eval day d)                                       # code
    if mode == learn and any feedback is an edit:
        lessons = apply_ops(lessons, learn(context, lessons, edits, tickets), d) # LLM
    write day d outputs; prev_plan = plan
```

- Source IDs: `D{d}-M{i}` for meetings, `D{d}-S{i}` for Slack messages, `D{d}-J{i}` for Jira changes, with `i` counting from 1 in file order and no zero padding.
- Open tickets are tickets whose status is not `done`.
- Interpret never sees lessons, plans or feedback. Its prompts are therefore identical in both modes, and the second run gets every Interpret call from the cache.
- Day 1 must come out identical in both modes, because there are no lessons yet. A difference means something other than lessons leaks between modes.
- Lessons learned on day d are first used on day d+1.
- Each `run` starts from day 1 and overwrites `runs/<name>/`. The cache makes reruns cheap.

## 7. The three LLM steps

For each step, the system prompt is the file in `prompts/`, read at call time. The user message is the sections listed, in the formats of the Appendix, separated by one blank line.

Checks are a little looser than the limits in the prompts on purpose, so a retry happens only on a real failure.

**Interpret** (`prompts/interpret.md`)
- User message: TODAY, PURPOSE, TEAM, ON-CALL, CUSTOMERS, RECENT HISTORY (day 1 only), YESTERDAY'S BRAIN, TODAY'S DATA, OPEN TICKETS.
- Output `Brain`: `summary: str`, `items: list[BrainItem]`. BrainItem: `type: "decision" | "commitment" | "blocker" | "request" | "risk" | "status"`, `text: str`, `person: str | None`, `ticket: str | None`, `sources: list[str]`.
- Checks: at most 25 items; summary at most 90 words; each text at most 45 words; `person` and `ticket` exist when set (the ticket may be done); 1 to 5 sources per item, each a source ID from today or an earlier day.

**Decide** (`prompts/decide.md`)
- User message: TODAY, PURPOSE, TEAM, ON-CALL, CUSTOMERS, BRAIN, OPEN TICKETS, LESSONS, YOUR PREVIOUS PLAN.
- Output `Plan`: `plans: list[PersonPlan]`. PersonPlan: `person`, `top3: list[PlanItem]`, `lessons_used: list[str]`, `note: str`. PlanItem: `ticket`, `hours: float`, `why`, `sources: list[str]`.
- Checks: exactly one PersonPlan per person; exactly 3 items with distinct, open ticket IDs; each `hours` between 0.5 and 8 and a person's total at most 8; `why` at most 40 words; `note` at most 60 words; 1 to 5 sources per item, each a known source ID or an existing ticket ID; `lessons_used` only contains IDs shown under LESSONS (so it is always empty in nolearn).
- Save the plans in the people order of `history.json`.

**Learn** (`prompts/learn.md`; learn mode only, and only on days with at least one edit)
- User message: TODAY, TEAM, CUSTOMERS, CURRENT LESSONS, CORRECTIONS (edits only; accepts are not shown).
- Output `LearnOutput`: `ops: list[LessonOp]`. LessonOp: `op: "add" | "update"`, `id: str | None`, `rule: str`, `applies_to: list[str]`, `from_people: list[str]`.
- Checks: at most 6 ops; `add` has no id; `update` names an existing lesson; rule at most 70 words; `applies_to` is `["all"]` or a non-empty list of person IDs; `from_people` is not empty and only lists people who edited today.
- Apply the ops in order. `add` creates `Lesson(id=f"L{len(lessons) + 1}", rule, applies_to, created_day=d, updated_day=d, evidence=[f"D{d}:{p}" for p in from_people])`. `update` replaces rule and applies_to, sets `updated_day=d` and appends to evidence. Lessons are never deleted.

## 8. LLM wrapper (`agent/llm.py`)

```
def call(step: str, day: int, system: str, user: str, out: type[M], check: Callable[[M], list[str]]) -> M
```

- Config from `.env` via python-dotenv: `LLM_PROVIDER` (`anthropic` or `openai`), `LLM_MODEL`, and that provider's API key. Use the provider's official SDK with one system prompt and one user message, at most 4096 output tokens, temperature 0. If the chosen model rejects temperature, leave it out and say so in your summary.
- Guard: `set_forbidden(texts)` stores every `hidden_fact`; the CLI calls it before any run. `call()` raises `RuntimeError`, naming the step and day, if a stored text of 20 characters or more appears in `system` or `user`. The guard runs before the cache lookup.
- Cache: key = sha256 of `json.dumps([provider, model, system, user])`. The raw reply is stored in `.cache/<key>.txt`. On a hit there is no API call.
- Parse: strip a surrounding ```json fence if present, `json.loads`, `out.model_validate`, then `check(result)`. Collect every error message from these stages.
- Retry: once, with the same system prompt and `user + "\n\nYour previous reply failed these checks:\n- " + "\n- ".join(errors) + "\n\nReply again with corrected JSON only."`. The retry is cached like any call.
- Second failure: write the raw reply to `<run dir>/failed_<step>_day<NN>.txt`, then raise `RuntimeError` with the step, day and errors.
- Log: one JSON line per attempt to `<run dir>/llm_calls.jsonl` with `step`, `day`, `attempt`, `cache_hit` and `seconds`. `start_run(run_dir)` sets the run directory.

## 9. Feedback and scoring (`agent/evaluate.py`)

```
WEIGHTS = (3, 2, 1)

def score_person(proposed: list[str], final: list[str]) -> float:
    earned = 0.0
    for i, ticket in enumerate(final):
        if proposed[i] == ticket:
            earned += WEIGHTS[i]
        elif ticket in proposed:
            earned += WEIGHTS[i] / 2
    return round(100 * earned / 6, 1)
```

- `simulate_feedback(plan, eval_day) -> list[Feedback]`: for each person, `proposed` is their plan's 3 ticket IDs. If `proposed == final`, the action is `accept` and `reason` is None. Otherwise the action is `edit`, with the final list and the reason. Feedback: `person`, `action`, `proposed`, `final`, `reason`. It never carries `hidden_fact`.
- `score_day(plan, eval_day) -> DayScore`: `day`, `date`, `mean` (average of the 6 person scores, 1 decimal), `accepts` (number of accepts), `people` (person ID to score).

## 10. Run outputs

```
runs/<name>/day_NN/brain.json       Interpret output
runs/<name>/day_NN/plan.json        Decide output
runs/<name>/day_NN/feedback.json    list of Feedback
runs/<name>/day_NN/score.json       DayScore
runs/<name>/day_NN/lessons.json     all lessons after this day ([] in nolearn)
runs/<name>/scores.csv              day,date,mean,accepts,maya,arjun,nina,leila,sam,jordan
runs/<name>/llm_calls.jsonl
runs/compare.png                    written by report
```

Write JSON with a 2-space indent, so diffs between runs are readable.

## 11. CLI (`agent/cli.py`, argparse)

Global option `--data PATH` (default `data`).

- `validate`: section 4.
- `run --mode {learn,nolearn} [--name NAME] [--days N]`: NAME defaults to the mode, `--days` runs days 1 to N (default: all). Runs `validate` first and stops on errors. Prints one line per day: `day 2  2026-10-20  mean 83.3  accepts 4/6  lessons 2`.
- `report RUN_A RUN_B`: prints one row per day with both means, the gap (B minus A) and both accept counts. Then saves `runs/compare.png`: a line chart, x = day, y = mean score from 0 to 100, one line per run with markers and a legend, titled "Agreement with the team, by day". If the day 1 `plan.json` files differ between the two runs, print a warning that something other than lessons changed between them.
- `show RUN --day N [--person ID]`: plain text for that day, one block per person (or the one asked for): the 3 proposed tickets with title, hours, why and sources; the lessons used, with the rule text Decide saw (from the previous day's `lessons.json`); the note; the feedback (accept, or edit with the final list and reason); the score. This is the demo screen until a UI exists, so make it easy to read.

## 12. Tests (pytest, no API calls)

- `test_evaluate.py`: with final = [A, B, C], `score_person` gives 100.0 for [A, B, C], 0.0 for [D, E, F], 58.3 for [B, A, C], 50.0 for [C, A, B], 50.0 for [A, X, Y] and 25.0 for [X, Y, A]. `simulate_feedback` returns accept with no reason for an exact match, and edit with the reason otherwise.
- `test_store.py`: create, update with `set`, and update with only a comment, on a small in-memory example; create of an existing ID raises; update of a missing ID raises; source IDs come out as D1-M1, D1-S1, D1-J1 ... in file order; `validate` passes on `data/`; on a broken copy in a temp dir (one unknown field, one unknown owner, one final ticket that is done) it reports all three errors.
- `test_leakage.py`: build every Interpret, Decide and Learn user message for days 1 and 2 from the real data with stub inputs (an empty brain, a stub plan where each person proposes the first 3 open tickets they own, one stub lesson, and corrections from `simulate_feedback` on the stub plan). Assert that no `hidden_fact` appears in any of them. Also assert that `llm.call` raises on a forbidden text without calling the provider (monkeypatch the provider function so the test fails if it is called).

## 13. Build order

Do these in order. Run each check before starting the next step. Commit after each step if the repo uses git.

1. `schemas.py`, `store.py`, eval loading and checks in `evaluate.py`, `cli validate`. Check: `python -m agent validate` prints `OK: 2 days, 32 tickets, 6 people`.
2. Scoring and feedback in `evaluate.py`, `test_evaluate.py`, `test_store.py`. Check: `pytest` passes.
3. `render.py` and `test_leakage.py`. Check: `pytest` passes. Print the day 2 Decide user message built from stubs, and compare it once against the Appendix.
4. `llm.py`. Check: one live call with a tiny prompt validates, the same call again is a cache hit, and the guard test passes.
5. Interpret and Decide in `steps.py`, and `cli run` without Learn. Check: `run --mode nolearn` finishes days 1 and 2, and `show nolearn --day 1` prints 6 readable plans.
6. Learn. Check: `run --mode learn` works; day 1 `lessons.json` is not empty; day 2 plans list `lessons_used`; day 1 `plan.json` and `score.json` are identical between the learn and nolearn runs.
7. `cli report` and `cli show`. Check: the report table prints and `runs/compare.png` exists.

Then stop and write the final summary.

## 14. Final summary

- Files created and the line count of each (`wc -l`).
- The output of `validate`, `report nolearn learn` and `pytest`.
- Every ambiguity you resolved, and the choice you made.
- Anything in this brief you could not do, and any prompt you think is wrong (without changing it).

## Appendix: prompt text formats

These are the exact formats, shown with day 2 values and shortened with `...`. Each section starts with its header line in capitals.

```
TODAY
Day 2, Tuesday 2026-10-20

PURPOSE
Tidewell: Dock-scheduling software for regional food distributors. Carriers book dock appointments; warehouses see their load for the day.
Mission: Make every truck's time at the dock predictable.
Goals, most important first:
G1 (rank 1): Launch Smart Slots at FreightWorks Expo. Metric: 3 design partners live on Smart Slots by launch. Due 2026-11-04.

TEAM
maya: Maya Chen, CEO. Owns: Sales, fundraising, executive relationships with key customers. Notes: Runs the Monday standup.
nina: Nina Kowalski, Backend engineer. Owns: Smart Slots ranking, data exports, alerting.

ON-CALL
Week of 2026-10-19: nina

CUSTOMERS
harbor: Harbor Foods. 38% of revenue. Renews 2026-10-30. Health: green. Contact: Dana Whitfield, VP Operations. Notes: Largest customer. ...

RECENT HISTORY
2026-09-28: Syncs failed for 3 hours and Harbor Foods lost 41 dock appointments. ...

YESTERDAY'S BRAIN
Summary: ...
- [blocker] Nina needs Arjun's review of PR #232 before ... | person: nina | ticket: TW-116 | sources: D2-S2

TODAY'S DATA
MEETING D2-M1 | 10:00 | Expo planning sync | attendees: maya, sam, jordan
Maya: Booth 412 is confirmed. Fifteen days. Sam, what does the demo look like?
...

SLACK
D2-S1 | 09:02 | #eng | arjun: Sep 28 postmortem is up in TW-101. ...

JIRA CHANGES
D2-J1 | by arjun | update TW-101 | status=done | comment: Postmortem written. ...
D2-J12 | by nina | update TW-116 | comment: PR #232 is ready for review.
D2-J13 | by nina | create TW-129 | title=Review PR #232: Smart Slots ranking | description=TW-116 cannot merge until this is reviewed. | owner=arjun | status=todo | priority=medium | goal=G1 | customer=null | due=null | estimate_hours=1

OPEN TICKETS
TW-116 [in_progress, high] Smart Slots: recommendation ranking
  owner nina | goal G1 | customer none | due 2026-10-27 | estimate 10h
  description: Rank suggested slots for each booking. Feeds the scoring API.
  comments: (day 2, nina) PR #232 is ready for review.

LESSONS
L1 (learned day 1, updated day 1, applies to: all): ...

YOUR PREVIOUS PLAN
maya: TW-126, TW-120, TW-111

CORRECTIONS
nina (Backend engineer)
  You proposed:
    1. TW-116 Smart Slots: recommendation ranking (owner nina, goal G1, customer none)
    2. ...
  They chose:
    1. TW-130 Harbor: Fresno DC appointments show UTC times (owner nina, goal G2, customer harbor)
    2. ...
  Their reason: ...
```

Format notes:
- Header variants: Decide prints the brain under `BRAIN`; Learn prints lessons under `CURRENT LESSONS`.
- Empty sections: YESTERDAY'S BRAIN on day 1 prints `(empty: this is the first day)`; LESSONS prints `(none yet)`; YOUR PREVIOUS PLAN on day 1 prints `(none: this is the first day)`.
- Leave out `Notes:` when notes are empty, the `description:` line when the description is empty, and the `comments:` line when there are none. Show at most the last 2 comments, joined with ` / `. Cut descriptions at 200 characters.
- Inside TODAY'S DATA, each meeting, the SLACK block and the JIRA CHANGES block are separated by one blank line. Transcript lines print as written.
- Jira `set` fields print as `key=value` in file order, with `null` for None. A null comment is left out.
- Numbers print without a trailing `.0` (use `f"{x:g}"`). Goals sort by rank; tickets and lessons sort by their number (TW-99 before TW-100, L9 before L10).
- The weekday in TODAY comes from the date.
