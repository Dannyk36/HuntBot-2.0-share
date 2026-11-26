from datetime import UTC, datetime
import hashlib
import json


def build_provenance(prompt_id: str, template_version: str, input_payload: dict) -> dict:
    payload_json = json.dumps(input_payload, sort_keys=True, separators=(",", ":"))
    input_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    return {
        "prompt_id": prompt_id,
        "template_version": template_version,
        "input_hash": input_hash,
        "timestamp": datetime.now(UTC).isoformat(),
    }
