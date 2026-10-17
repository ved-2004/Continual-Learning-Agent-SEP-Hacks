from datetime import date as Date
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

CFG = ConfigDict(extra="forbid", allow_inf_nan=False)
PersonId = Annotated[str, Field(pattern=r"^[a-z]+$")]
TicketId = Annotated[str, Field(pattern=r"^TW-[0-9]+$")]
Clock = Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]
Status = Literal["todo", "in_progress", "blocked", "done"]
Priority = Literal["high", "medium", "low"]

class Goal(BaseModel):
    model_config = CFG
    id: Annotated[str, Field(pattern=r"^G[0-9]+$")]
    rank: Annotated[int, Field(gt=0)]
    goal: str; metric: str; due: Date

class Purpose(BaseModel):
    model_config = CFG
    company: str; what_we_do: str; mission: str
    goals: list[Goal]

class Person(BaseModel):
    model_config = CFG
    id: PersonId; name: str; role: str; owns: str; notes: str

class Customer(BaseModel):
    model_config = CFG
    id: str; name: str; arr_share_pct: float; renewal_date: Date
    health: Literal["green", "yellow", "red"]
    contact: str; notes: str

class OnCall(BaseModel):
    model_config = CFG
    week_of: Date; person: PersonId

class Event(BaseModel):
    model_config = CFG
    date: Date; text: str

class Comment(BaseModel):
    model_config = CFG
    day: int; by: PersonId; text: str

class Ticket(BaseModel):
    model_config = CFG
    id: TicketId; title: str; description: str; owner: PersonId
    status: Status; priority: Priority
    goal: str | None; customer: str | None; due: Date | None; estimate_hours: float | None
    comments: list[Comment] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def runtime_comments(cls, value, info):
        if info.context and info.context.get("input") and isinstance(value, dict) and "comments" in value:
            raise ValueError("comments are runtime only, never allowed in input files")
        return value

class TicketPatch(BaseModel):
    model_config = CFG
    title: str | None = None
    description: str | None = None
    owner: PersonId | None = None
    status: Status | None = None
    priority: Priority | None = None
    goal: str | None = None
    customer: str | None = None
    due: Date | None = None
    estimate_hours: float | None = None

class History(BaseModel):
    model_config = CFG
    as_of: Date
    people: Annotated[list[Person], Field(min_length=6, max_length=6)]
    customers: list[Customer]; oncall: list[OnCall]; tickets: list[Ticket]; events: list[Event]

class Meeting(BaseModel):
    model_config = CFG
    title: str; time: Clock; attendees: list[PersonId]; transcript: list[str]

class SlackMessage(BaseModel):
    model_config = CFG
    channel: Literal["#general", "#eng", "#design", "#customers"]
    author: PersonId; time: Clock; text: str

class JiraChange(BaseModel):
    model_config = CFG
    ticket: TicketId; action: Literal["create", "update"]; by: PersonId
    set: TicketPatch; comment: str | None

class Day(BaseModel):
    model_config = CFG
    day: Annotated[int, Field(gt=0)]; date: Date
    meetings: list[Meeting]; slack: list[SlackMessage]; jira: list[JiraChange]

class EvalRecord(BaseModel):
    model_config = CFG
    person: PersonId; final: list[TicketId]; reason: str; hidden_fact: str

class EvalDay(BaseModel):
    model_config = CFG
    day: int; records: list[EvalRecord]

class BrainItem(BaseModel):
    model_config = CFG
    type: Literal["decision", "commitment", "blocker", "request", "risk", "status"]
    text: str; person: str | None; ticket: str | None; sources: list[str]

class Brain(BaseModel):
    model_config = CFG
    summary: str; items: list[BrainItem]

class PlanItem(BaseModel):
    model_config = CFG
    ticket: str; hours: float; why: str; sources: list[str]

class PersonPlan(BaseModel):
    model_config = CFG
    person: str; top3: list[PlanItem]; lessons_used: list[str]; note: str

class Plan(BaseModel):
    model_config = CFG
    plans: list[PersonPlan]

class LessonOp(BaseModel):
    model_config = CFG
    op: Literal["add", "update"]
    id: str | None = None
    rule: str; applies_to: list[str]; from_people: list[str]

class LearnOutput(BaseModel):
    model_config = CFG
    ops: list[LessonOp]

class Lesson(BaseModel):
    model_config = CFG
    id: str; rule: str; applies_to: list[str]
    created_day: int; updated_day: int; evidence: list[str]

class Feedback(BaseModel):
    model_config = CFG
    person: str; action: Literal["accept", "edit"]
    proposed: list[str]; final: list[str]; reason: str | None

class DayScore(BaseModel):
    model_config = CFG
    day: int; date: Date; mean: float; accepts: int; people: dict[str, float]
