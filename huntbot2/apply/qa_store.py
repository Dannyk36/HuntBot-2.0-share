"""Persistent store for form Q&A — learned answers + pending questions."""
from __future__ import annotations

import json
from pathlib import Path


def _qa_path(project_dir: Path) -> Path:
    return Path(project_dir) / "config" / "form_qa.json"


def _pending_path(project_dir: Path) -> Path:
    return Path(project_dir) / "data" / "pending_questions.json"


# ── Learned answers ───────────────────────────────────────────────────────────

def load_learned_answers(project_dir: Path) -> dict[str, str]:
    """Return {question_label: answer} for all saved answers."""
    p = _qa_path(project_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def save_learned_answer(project_dir: Path, label: str, answer: str) -> None:
    """Persist a single question → answer pair."""
    answers = load_learned_answers(project_dir)
    answers[label] = answer
    p = _qa_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(answers, indent=2, ensure_ascii=False))


def save_learned_answers_bulk(project_dir: Path, new_answers: dict[str, str]) -> None:
    """Merge multiple answers into the store."""
    answers = load_learned_answers(project_dir)
    answers.update({k: v for k, v in new_answers.items() if v is not None})
    p = _qa_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(answers, indent=2, ensure_ascii=False))


# ── Pending questions ─────────────────────────────────────────────────────────

def load_pending_questions(project_dir: Path) -> list[dict]:
    """Return list of {label, type, options, job_id, job_title, company}."""
    p = _pending_path(project_dir)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:
        return []


def add_pending_questions(
    project_dir: Path,
    questions: list[dict],
    *,
    job: dict,
) -> None:
    """
    Add unanswered questions to the pending list, deduplicating by label.
    Each question dict should have {label, type, options}.
    """
    existing = load_pending_questions(project_dir)
    existing_labels = {q["label"] for q in existing}
    learned = load_learned_answers(project_dir)

    for q in questions:
        label = q.get("label", "")
        # Skip if already answered or already pending
        if not label or label in existing_labels or label in learned:
            continue
        existing.append({
            "label": label,
            "type": q.get("type", "text"),
            "options": q.get("options"),
            "job_id": job.get("id"),
            "job_title": job.get("title", ""),
            "company": job.get("company", ""),
            "answer": "",
        })
        existing_labels.add(label)

    p = _pending_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(existing, indent=2, ensure_ascii=False))


def resolve_pending_question(project_dir: Path, label: str, answer: str) -> None:
    """Mark a pending question as answered and save to learned answers."""
    save_learned_answer(project_dir, label, answer)
    pending = load_pending_questions(project_dir)
    pending = [q for q in pending if q["label"] != label]
    p = _pending_path(project_dir)
    p.write_text(json.dumps(pending, indent=2, ensure_ascii=False))


def clear_pending_questions(project_dir: Path) -> None:
    p = _pending_path(project_dir)
    if p.exists():
        p.write_text("[]")
