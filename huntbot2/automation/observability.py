from __future__ import annotations

from datetime import datetime, UTC
import json
from pathlib import Path


def write_pipeline_observability(project_dir: Path, run_summary: dict) -> Path:
    artifacts_dir = project_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC)
    ts_file = now.strftime("%Y%m%d-%H%M%S")
    ts_iso = now.isoformat()

    stages = run_summary.get("stages", [])
    total_retries = sum(int(stage.get("retries_used", 0)) for stage in stages)
    failed_stages = [stage.get("stage") for stage in stages if stage.get("status") == "failed"]

    payload = {
        "run_id": run_summary.get("run_id"),
        "timestamp_utc": ts_iso,
        "status": run_summary.get("status"),
        "mode": run_summary.get("mode"),
        "dry_run": run_summary.get("dry_run"),
        "max_retries": run_summary.get("max_retries"),
        "submit_policy": run_summary.get("submit_policy"),
        "apply_gate_decision": run_summary.get("apply_gate_decision"),
        "queue_snapshot": run_summary.get("queue_snapshot"),
        "stage_metrics": stages,
        "total_retries": total_retries,
        "failures": failed_stages,
    }

    out_path = artifacts_dir / f"pipeline-observability-{ts_file}.json"
    out_path.write_text(json.dumps(payload, indent=2))
    return out_path
