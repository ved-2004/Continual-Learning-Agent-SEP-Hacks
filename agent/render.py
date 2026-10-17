from agent.store import source_items


def section(header, body):
    return header + "\n" + body


def number(value):
    return "none" if value is None else f"{value:g}" if isinstance(value, (int, float)) else str(value)


def today(day):
    return section("TODAY", f"Day {day.day}, {day.date:%A %Y-%m-%d}")


def team(history):
    return section("TEAM", "\n".join(f"{p.id}: {p.name}, {p.role}. Owns: {p.owns}." +
        (f" Notes: {p.notes}" if p.notes else "") for p in history.people))


def customers(history):
    return section("CUSTOMERS", "\n".join(f"{c.id}: {c.name.rstrip('.') }. {c.arr_share_pct:g}% of revenue. "
        f"Renews {c.renewal_date}. Health: {c.health}. Contact: {c.contact}." +
        (f" Notes: {c.notes}" if c.notes else "") for c in history.customers))


def context(purpose, history, day):
    goals = "\n".join(f"{g.id} (rank {g.rank}): {g.goal}. Metric: {g.metric}. Due {g.due}."
                      for g in sorted(purpose.goals, key=lambda g: g.rank))
    return [today(day), section("PURPOSE", f"{purpose.company}: {purpose.what_we_do}\nMission: {purpose.mission}\n"
        f"Goals, most important first:\n{goals}"), team(history),
        section("ON-CALL", "\n".join(f"Week of {o.week_of}: {o.person}" for o in history.oncall)), customers(history)]


def brain_text(brain, header):
    if brain is None:
        return section(header, "(empty: this is the first day)")
    return section(header, "\n".join([f"Summary: {brain.summary}"] + [
        f"- [{b.type}] {b.text} | person: {b.person or 'none'} | ticket: {b.ticket or 'none'} | sources: {', '.join(b.sources)}"
        for b in brain.items]))


def open_tickets(tickets):
    blocks = []
    for t in sorted(tickets.values(), key=lambda t: int(t.id.split('-')[1])):
        if t.status == "done":
            continue
        lines = [f"{t.id} [{t.status}, {t.priority}] {t.title}", f"  owner {t.owner} | goal {t.goal or 'none'} | "
                 f"customer {t.customer or 'none'} | due {number(t.due)} | estimate {number(t.estimate_hours)}" +
                 ("h" if t.estimate_hours is not None else "")]
        if t.description:
            lines.append(f"  description: {t.description[:200]}")
        if t.comments:
            lines.append("  comments: " + " / ".join(f"(day {c.day}, {c.by}) {c.text}" for c in t.comments[-2:]))
        blocks.append("\n".join(lines))
    return section("OPEN TICKETS", "\n\n".join(blocks))


def data_text(day):
    meetings, slack, jira = [], [], []
    for source, item in source_items(day):
        if "-M" in source:
            meetings.append(f"MEETING {source} | {item.time} | {item.title} | attendees: {', '.join(item.attendees)}\n" + "\n".join(item.transcript))
        elif "-S" in source:
            slack.append(f"{source} | {item.time} | {item.channel} | {item.author}: {item.text}")
        else:
            patch = item.set.model_dump(exclude_unset=True)
            fields = [f"{key}={'null' if patch[key] is None else number(patch[key])}" for key in item.set._order]
            jira.append(" | ".join([f"{source}", f"by {item.by}", f"{item.action} {item.ticket}"] + fields +
                                  ([f"comment: {item.comment}"] if item.comment is not None else [])))
    return section("TODAY'S DATA", "\n\n".join(meetings + [section("SLACK", "\n".join(slack)), section("JIRA CHANGES", "\n".join(jira))]))


def lesson_text(lessons, header):
    return section(header, "\n".join(f"{l.id} (learned day {l.created_day}, updated day {l.updated_day}, "
        f"applies to: {', '.join(l.applies_to)}): {l.rule}" for l in sorted(lessons, key=lambda l: int(l.id[1:]))) or "(none yet)")


def interpret_message(purpose, history, day, brain, tickets):
    parts = context(purpose, history, day)
    if day.day == 1:
        parts.append(section("RECENT HISTORY", "\n".join(f"{e.date}: {e.text}" for e in history.events)))
    return "\n\n".join(parts + [brain_text(brain, "YESTERDAY'S BRAIN"), data_text(day), open_tickets(tickets)])


def decide_message(purpose, history, day, brain, tickets, lessons, previous):
    previous = "\n".join(f"{p.person}: {', '.join(i.ticket for i in p.top3)}" for p in previous.plans) if previous else "(none: this is the first day)"
    return "\n\n".join(context(purpose, history, day) + [brain_text(brain, "BRAIN"), open_tickets(tickets),
        lesson_text(lessons, "LESSONS"), section("YOUR PREVIOUS PLAN", previous)])


def learn_message(history, day, lessons, feedback, tickets):
    roles, corrections = {p.id: p.role for p in history.people}, []
    for f in feedback:
        if f.action != "edit":
            continue
        lines = [f"{f.person} ({roles[f.person]})"]
        for label, ids in (("You proposed:", f.proposed), ("They chose:", f.final)):
            lines.append(f"  {label}")
            for i, key in enumerate(ids, 1):
                t = tickets[key]
                lines.append(f"    {i}. {key} {t.title} (owner {t.owner}, goal {t.goal or 'none'}, customer {t.customer or 'none'})")
        lines.append(f"  Their reason: {f.reason}")
        corrections.append("\n".join(lines))
    return "\n\n".join([today(day), team(history), customers(history), lesson_text(lessons, "CURRENT LESSONS"),
                         section("CORRECTIONS", "\n\n".join(corrections))])
