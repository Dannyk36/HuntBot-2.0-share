import json
from datetime import UTC, datetime
from pathlib import Path


def write_apply_evidence(
    project_dir: str | Path,
    run_id: int,
    mode: str,
    dry_run: bool,
    submit_policy: str,
    token_present: bool,
    gate_decision: str,
) -> list[str]:
    """Write deterministic apply evidence placeholder files and return their paths."""
    root = Path(project_dir).resolve()
    artifacts_dir = root / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    run_tag = f"run-{run_id:04d}"
    files = [
        artifacts_dir / f"apply-step-open-{run_tag}.txt",
        artifacts_dir / f"apply-step-review-{run_tag}.md",
    ]

    files[0].write_text(
        "\n".join(
            [
                "# Apply Evidence Placeholder",
                "step: open",
                f"run_id: {run_id}",
                f"mode: {mode}",
                f"dry_run: {str(dry_run).lower()}",
                f"submit_policy: {submit_policy}",
                f"token_present: {str(token_present).lower()}",
                f"gate_decision: {gate_decision}",
            ]
        )
        + "\n"
    )
    files[1].write_text(
        "\n".join(
            [
                "# Apply Evidence Placeholder",
                "step: review",
                f"run_id: {run_id}",
                f"mode: {mode}",
                f"dry_run: {str(dry_run).lower()}",
                f"submit_policy: {submit_policy}",
                f"token_present: {str(token_present).lower()}",
                f"gate_decision: {gate_decision}",
            ]
        )
        + "\n"
    )

    return [str(path) for path in files]


def write_apply_probe_artifact(
    project_dir: str | Path,
    run_id: int,
    probe_result: dict,
    screenshot_path: str | None = None,
) -> str:
    """Write probe evidence JSON artifact and return its path."""
    root = Path(project_dir).resolve()
    artifacts_dir = root / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    run_tag = f"run-{run_id:04d}"
    out_path = artifacts_dir / f"apply-probe-{run_tag}.json"

    payload = dict(probe_result)
    if screenshot_path is not None:
        payload["screenshot_path"] = screenshot_path

    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return str(out_path)


def write_apply_decision_artifact(
    project_dir: str | Path,
    run_id: int,
    login_state: str,
    apply_path: str,
    next_action: str,
    reasons: list[str],
    signals: list[str],
) -> str:
    root = Path(project_dir).resolve()
    artifacts_dir = root / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    run_tag = f"run-{run_id:04d}"
    out_path = artifacts_dir / f"apply-decision-{run_tag}.json"

    payload = {
        "login_state": login_state,
        "apply_path": apply_path,
        "next_action": next_action,
        "reasons": reasons,
        "signals": signals,
    }
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return str(out_path)


def write_apply_queue_artifact(
    project_dir: str | Path,
    run_id: int,
    next_action: str,
    queue_name: str,
    queue_code: str,
) -> str:
    root = Path(project_dir).resolve()
    artifacts_dir = root / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    run_tag = f"run-{run_id:04d}"
    out_path = artifacts_dir / f"apply-queue-{run_tag}.json"
    payload = {
        "run_id": run_id,
        "next_action": next_action,
        "queue_name": queue_name,
        "queue_code": queue_code,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return str(out_path)
