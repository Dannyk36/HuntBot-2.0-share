from dataclasses import dataclass


@dataclass
class ScheduleEntry:
    time_utc: str
    stages: list[str]


@dataclass
class ProactiveCheckinPlan:
    time_utc: str
    prompt: str


def build_automation_plan() -> list[ScheduleEntry]:
    """Return a simple deterministic 3x daily automation plan."""
    stage_order = ["discover", "score", "tailor", "apply"]
    times = ["09:00", "13:00", "18:00"]
    return [ScheduleEntry(time_utc=t, stages=stage_order.copy()) for t in times]


def build_proactive_checkin_plan() -> ProactiveCheckinPlan:
    """Return daily proactive check-in template without duplicating pipeline stages."""
    return ProactiveCheckinPlan(
        time_utc="08:30",
        prompt=(
            "Daily proactive check-in: ask for today's single highest-priority outcome, "
            "then suggest 1-3 automations and one risk to watch based on prior run history."
        ),
    )
