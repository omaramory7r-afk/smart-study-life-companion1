"""Deterministic weekly study scheduler with deadlines, priorities and fixed commitments."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

DAY_NAMES = ["الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"]


def to_minutes(value: str) -> int:
    try:
        hour, minute = value.split(":", 1)
        h, m = int(hour), int(minute)
    except (ValueError, AttributeError):
        raise ValueError("اكتب الوقت بصيغة HH:MM مثل 16:30.")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("وقت غير صالح. استخدم صيغة 24 ساعة HH:MM.")
    return h * 60 + m


def format_time(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


class StudySubject(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=100)
    color: str | None = Field(default=None, max_length=24)
    weekly_target_minutes: int | None = Field(default=None, ge=0, le=10080)


class StudyTask(BaseModel):
    subject: str = Field(min_length=1, max_length=100)
    subject_id: str | None = Field(default=None, min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=180)
    duration_minutes: int = Field(ge=10, le=600)
    priority: int = Field(ge=1, le=5)
    difficulty: int = Field(default=3, ge=1, le=5)
    deadline_day: int = Field(ge=0, le=6)
    deadline_time: str = "23:59"

    @field_validator("deadline_time")
    @classmethod
    def validate_deadline_time(cls, value: str) -> str:
        to_minutes(value)
        return value


class AvailabilityWindow(BaseModel):
    day: int = Field(ge=0, le=6)
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def validate_time(cls, value: str) -> str:
        to_minutes(value)
        return value


class BusyWindow(BaseModel):
    day: int = Field(ge=0, le=6)
    start: str
    end: str
    title: str = Field(default="التزام ثابت", max_length=100)

    @field_validator("start", "end")
    @classmethod
    def validate_time(cls, value: str) -> str:
        to_minutes(value)
        return value


class ScheduleRequest(BaseModel):
    subjects: list[StudySubject] = Field(default_factory=list, max_length=30)
    tasks: list[StudyTask] = Field(min_length=1, max_length=100)
    availability: list[AvailabilityWindow] = Field(min_length=1, max_length=70)
    busy_windows: list[BusyWindow] = Field(default_factory=list, max_length=100)
    break_minutes: int = Field(default=10, ge=0, le=60)
    max_session_minutes: int = Field(default=60, ge=25, le=120)
    min_session_minutes: int = Field(default=25, ge=10, le=60)

    @model_validator(mode="after")
    def validate_subject_links(self):
        ids = [subject.id for subject in self.subjects]
        if len(ids) != len(set(ids)):
            raise ValueError("معرّفات المواد يجب أن تكون فريدة.")
        known = set(ids)
        for task in self.tasks:
            if task.subject_id and task.subject_id not in known:
                raise ValueError(f"المادة المرتبطة بالمهمة {task.title} غير موجودة.")
        if self.min_session_minutes > self.max_session_minutes:
            raise ValueError("الحد الأدنى للجلسة لا يمكن أن يتجاوز الحد الأقصى.")
        return self


def _normalize_windows(request: ScheduleRequest) -> tuple[list[dict[str, int]], int, int]:
    windows: list[dict[str, int]] = []
    for item in request.availability:
        start, end = to_minutes(item.start), to_minutes(item.end)
        if end <= start:
            raise ValueError("وقت نهاية فترة الفراغ يجب أن يكون بعد بدايتها في اليوم نفسه.")
        windows.append({"day": item.day, "start": start, "end": end})

    windows.sort(key=lambda w: (w["day"], w["start"]))
    for previous, current in zip(windows, windows[1:]):
        if previous["day"] == current["day"] and current["start"] < previous["end"]:
            raise ValueError(f"هناك فترتان متداخلتان يوم {DAY_NAMES[current['day']]}. عدّل أوقات الفراغ أولاً.")

    busy_by_day: dict[int, list[tuple[int, int]]] = {}
    for item in request.busy_windows:
        start, end = to_minutes(item.start), to_minutes(item.end)
        if end <= start:
            raise ValueError("وقت نهاية الالتزام يجب أن يكون بعد بدايته في اليوم نفسه.")
        busy_by_day.setdefault(item.day, []).append((start, end))

    # Merge overlapping fixed commitments; they represent unavailable time, not errors.
    merged_busy: dict[int, list[tuple[int, int]]] = {}
    for day, intervals in busy_by_day.items():
        merged: list[list[int]] = []
        for start, end in sorted(intervals):
            if not merged or start > merged[-1][1]:
                merged.append([start, end])
            else:
                merged[-1][1] = max(merged[-1][1], end)
        merged_busy[day] = [(a, b) for a, b in merged]

    slots: list[dict[str, int]] = []
    total_free = 0
    committed = 0
    for window in windows:
        day = window["day"]
        cursor = window["start"]
        for busy_start, busy_end in merged_busy.get(day, []):
            overlap_start = max(window["start"], busy_start)
            overlap_end = min(window["end"], busy_end)
            if overlap_start >= overlap_end:
                continue
            if cursor < overlap_start:
                slots.append({"day": day, "start": cursor, "end": overlap_start, "cursor": cursor})
                total_free += overlap_start - cursor
            committed += overlap_end - overlap_start
            cursor = max(cursor, overlap_end)
        if cursor < window["end"]:
            slots.append({"day": day, "start": cursor, "end": window["end"], "cursor": cursor})
            total_free += window["end"] - cursor

    slots = [slot for slot in slots if slot["end"] > slot["start"]]
    return slots, total_free, committed


def generate_schedule(request: ScheduleRequest) -> dict[str, Any]:
    """Allocate study sessions using deadline slack, priority and difficulty.

    The algorithm is deterministic and does not require an AI provider. Priority is
    a meaningful tie-break/urgency factor, while deadline capacity prevents tasks
    with little remaining time from being postponed behind less urgent work.
    """
    slots, available_minutes, committed_minutes = _normalize_windows(request)
    analyses: list[dict[str, Any]] = []
    task_meta: dict[int, dict[str, Any]] = {}

    for index, task in enumerate(request.tasks):
        due_abs = task.deadline_day * 1440 + to_minutes(task.deadline_time)
        capacity = 0
        for slot in slots:
            slot_start = slot["day"] * 1440 + slot["start"]
            slot_end = slot["day"] * 1440 + slot["end"]
            if slot_start < due_abs:
                capacity += max(0, min(slot_end, due_abs) - slot_start)
        slack = capacity - task.duration_minutes
        # Each priority step adds 45 minutes of urgency; difficulty adds a smaller
        # nudge so difficult work is not repeatedly deferred.
        urgency = slack - ((task.priority - 1) * 45) - ((task.difficulty - 1) * 10)
        meta = {
            "task_index": index,
            "subject": task.subject,
            "subject_id": task.subject_id,
            "title": task.title,
            "priority": task.priority,
            "difficulty": task.difficulty,
            "deadline_day": task.deadline_day,
            "deadline_time": task.deadline_time,
            "available_before_deadline_minutes": capacity,
            "slack_minutes": slack,
            "urgency_score": urgency,
        }
        analyses.append(meta)
        task_meta[index] = meta

    # Least slack first, then the actual deadline, then priority/difficulty.
    order = sorted(
        range(len(request.tasks)),
        key=lambda i: (
            task_meta[i]["urgency_score"],
            task_meta[i]["deadline_day"],
            to_minutes(task_meta[i]["deadline_time"]),
            -task_meta[i]["priority"],
            -task_meta[i]["difficulty"],
            i,
        ),
    )

    scheduled: list[dict[str, Any]] = []
    unscheduled: list[dict[str, Any]] = []
    day_cooldown: dict[int, int] = {}
    for index in order:
        task = request.tasks[index]
        meta = task_meta[index]
        due_abs = task.deadline_day * 1440 + to_minutes(task.deadline_time)
        remaining = task.duration_minutes
        task_sessions: list[dict[str, Any]] = []

        for slot in slots:
            if remaining <= 0:
                break
            slot["cursor"] = max(slot["cursor"], day_cooldown.get(slot["day"], slot["cursor"]))
            day_abs = slot["day"] * 1440
            if day_abs + slot["cursor"] >= due_abs:
                continue
            latest_end = min(slot["end"], due_abs - day_abs)
            while remaining > 0 and slot["cursor"] < latest_end:
                free = latest_end - slot["cursor"]
                if free <= 0:
                    break

                if remaining <= request.max_session_minutes and remaining <= free:
                    chunk = remaining
                else:
                    chunk = min(remaining, request.max_session_minutes, free)
                    # Avoid a tiny final fragment: e.g. 70 minutes becomes 45 + 25.
                    leftover = remaining - chunk
                    if (
                        0 < leftover < request.min_session_minutes
                        and remaining > request.min_session_minutes
                    ):
                        adjusted = remaining - request.min_session_minutes
                        if request.min_session_minutes <= adjusted <= free and adjusted <= request.max_session_minutes:
                            chunk = adjusted

                if chunk < request.min_session_minutes and chunk != remaining:
                    break
                if chunk <= 0:
                    break

                start = slot["cursor"]
                finish = start + chunk
                session = {
                    "day": slot["day"],
                    "start": format_time(start),
                    "end": format_time(finish),
                    "duration_minutes": chunk,
                    "subject": task.subject,
                    "subject_id": task.subject_id,
                    "title": task.title,
                    "priority": task.priority,
                    "difficulty": task.difficulty,
                    "task_index": index,
                    "deadline_day": task.deadline_day,
                    "deadline_time": task.deadline_time,
                }
                task_sessions.append(session)
                slot["cursor"] = finish + request.break_minutes
                day_cooldown[slot["day"]] = max(day_cooldown.get(slot["day"], 0), slot["cursor"])
                remaining -= chunk

        scheduled.extend(task_sessions)
        if remaining > 0:
            capacity = meta["available_before_deadline_minutes"]
            if capacity < task.duration_minutes:
                reason = (
                    f"المتاح قبل الموعد النهائي {capacity} دقيقة فقط، "
                    f"بينما تحتاج المهمة إلى {task.duration_minutes} دقيقة."
                )
            else:
                reason = "الوقت موجود إجمالاً، لكن لم يمكن توزيعه في جلسات مناسبة قبل الموعد النهائي."
            unscheduled.append({
                **{k: meta[k] for k in ("subject", "subject_id", "title", "priority", "deadline_day", "deadline_time")},
                "remaining_minutes": remaining,
                "requested_minutes": task.duration_minutes,
                "available_before_deadline_minutes": capacity,
                "reason": reason,
            })

    scheduled.sort(key=lambda x: (x["day"], x["start"], -x["priority"]))
    total_requested = sum(task.duration_minutes for task in request.tasks)
    total_scheduled = sum(item["duration_minutes"] for item in scheduled)
    used = total_scheduled
    return {
        "schedule": scheduled,
        "unscheduled": unscheduled,
        "task_analysis": sorted(analyses, key=lambda x: x["task_index"]),
        "summary": {
            "tasks_count": len(request.tasks),
            "scheduled_sessions": len(scheduled),
            "requested_minutes": total_requested,
            "scheduled_minutes": total_scheduled,
            "completion_percentage": round(total_scheduled / total_requested * 100) if total_requested else 0,
            "unscheduled_tasks": len(unscheduled),
            "available_minutes": available_minutes,
            "committed_minutes": committed_minutes,
            "unused_available_minutes": max(0, available_minutes - used),
            "break_minutes": request.break_minutes,
        },
        "day_names": DAY_NAMES,
    }
