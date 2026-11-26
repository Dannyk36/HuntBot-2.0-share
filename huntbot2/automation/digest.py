from datetime import UTC, datetime
from pathlib import Path


def write_pipeline_digest(
    project_dir: str | Path,
    mode: str,
    dry_run: bool,
    stage_results: list[dict],
    apply_gate_decision: str | None = None,
    submit_policy: str = "never_submit",
    apply_decision_summary: str | None = None,
    apply_queue_name: str | None = None,
    apply_queue_code: str | None = None,
    queue_snapshot: dict[str, int] | None = None,
    improvement_hooks: list[str] | None = None,
) -> Path:
    root = Path(project_dir).resolve()
    artifacts_dir = root / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC)
    filename = f"pipeline-digest-{now.strftime('%Y%m%d-%H%M%S')}.md"
    out_path = artifacts_dir / filename

    failures = [s for s in stage_results if s.get("status") == "failed"]
    if failures:
        next_action = f"Investigate stage `{failures[0]['stage']}` and rerun `pipeline-now` after remediation."
    elif not dry_run:
        next_action = "Pipeline reached non-dry-run apply path; resolve policy gate before live submit."
    else:
        next_action = "Dry-run completed successfully. Review artifacts and proceed when ready."

    lines = [
        "# HuntBot Pipeline Digest",
        "",
        f"- Run timestamp (UTC): {now.isoformat()}",
        f"- Mode: {mode}",
        f"- Dry run: {str(dry_run).lower()}",
        f"- Submit policy: {submit_policy}",
        f"- Apply gate decision: {apply_gate_decision or 'n/a'}",
        f"- Apply queue: {f'{apply_queue_name} ({apply_queue_code})' if apply_queue_name and apply_queue_code else 'n/a'}",
        "",
    ]

    if queue_snapshot is not None:
        lines.extend(
            [
                "## Queue Snapshot",
                "",
                f"- pending: {queue_snapshot.get('pending', 0)}",
                f"- processing: {queue_snapshot.get('processing', 0)}",
                f"- blocked: {queue_snapshot.get('blocked', 0)}",
                f"- resolved: {queue_snapshot.get('resolved', 0)}",
                f"- failed: {queue_snapshot.get('failed', 0)}",
                "",
            ]
        )

    lines.extend([
        "## Stage Results",
        "",
        "| Stage | Status | Duration (ms) | Retries Used | Error |",
        "|---|---|---:|---:|---|",
    ])

    for s in stage_results:
        error = (s.get("error") or "").replace("\n", " ")
        lines.append(
            f"| {s['stage']} | {s['status']} | {s['duration_ms']} | {s['retries_used']} | {error} |"
        )

    lines.extend(["", "## Failures", ""])
    if failures:
        for f in failures:
            lines.append(f"- `{f['stage']}`: {f.get('error') or 'unknown error'}")
    else:
        lines.append("- None")

    if apply_decision_summary:
        lines.append(f"- Apply decision: {apply_decision_summary}")

    lines.extend(["", "## Next Action Recommendation", "", f"- {next_action}"])
    if apply_decision_summary:
        lines.append(f"- Decision route context: {apply_decision_summary}")
    if apply_queue_name and apply_queue_code:
        lines.append(f"- Queue route: {apply_queue_name} ({apply_queue_code})")
    lines.append("")

    lines.extend(["## Improvement Hooks", ""])
    hooks = improvement_hooks or []
    if hooks:
        lines.extend([f"- {hook}" for hook in hooks])
    else:
        lines.append("- None")
    lines.append("")

    out_path.write_text("\n".join(lines))
    return out_path
