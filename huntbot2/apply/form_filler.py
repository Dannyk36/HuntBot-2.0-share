from __future__ import annotations

import json


def fill_form_with_llm(
    page,
    job: dict,
    profile: dict,
    llm_client,
    *,
    profile_yaml: str = "",
    master_resume: str = "",
    learned_answers: dict | None = None,
) -> dict:
    """
    Read visible form fields on page, ask LLM for best answers, fill them in.
    Returns {fields_filled, fields_skipped, skipped_labels, answers}.
    Never clicks submit — caller decides based on submit policy.

    profile_yaml: full raw YAML text from candidate_profile.yml (richer than the flat dict)
    master_resume: full text of master_resume.md for experience/skills context
    """
    fields: list[dict] = page.evaluate("""() => {
        const els = [];
        for (const el of document.querySelectorAll(
            'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]), select, textarea'
        )) {
            const rect = el.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) continue;
            const label = (el.labels && el.labels[0] && el.labels[0].textContent.trim())
                || el.placeholder || el.getAttribute('aria-label') || el.name || el.id || '';
            if (!label) continue;
            const options = el.tagName === 'SELECT'
                ? Array.from(el.options).map(o => o.text.trim())
                : null;
            els.push({label, type: el.type || el.tagName.toLowerCase(),
                       name: el.name || '', id: el.id || '', options});
        }
        return els;
    }""")

    if not fields:
        return {"fields_filled": 0, "fields_skipped": 0, "skipped_labels": [], "answers": {}}

    # Pre-fill from saved learned answers — these take priority over LLM
    known: dict[str, str] = dict(learned_answers or {})

    fields_desc = "\n".join(
        f"- {f['label']} ({f['type']})"
        + (f": options={f['options']}" if f["options"] else "")
        for f in fields
    )

    context_parts = [
        f"Job: {job.get('title', '')} at {job.get('company', '')} — {job.get('location', '')}",
    ]
    if profile_yaml:
        context_parts.append(f"CANDIDATE PROFILE (full YAML):\n{profile_yaml}")
    elif profile:
        context_parts.append(f"CANDIDATE PROFILE:\n{json.dumps(profile, indent=2)}")
    if master_resume:
        context_parts.append(f"CANDIDATE RESUME:\n{master_resume[:3000]}")

    system = (
        "You are an expert job application assistant filling out online application forms "
        "for the candidate described in the supplied profile and resume. "
        "Answer EVERY field — do not leave any blank. "
        "For questions about years of experience, calculate from the employment history. "
        "For salary questions, use the compensation values in the profile. "
        "For yes/no authorization questions, use the work_authorization section. "
        "For fields you cannot determine precisely, give the most reasonable answer "
        "based on the candidate's background. "
        "Return ONLY valid JSON — no explanation, no markdown fences."
    )
    prompt = (
        "\n\n".join(context_parts)
        + f"\n\nFORM FIELDS TO FILL:\n{fields_desc}\n\n"
        + "Return a JSON object with EVERY field label as a key and the best answer as the value.\n"
        + json.dumps({f["label"]: "" for f in fields}, indent=2)
    )

    llm_answers: dict = {}
    if llm_client is not None:
        try:
            from huntbot2.llm.parsing import extract_json_text
            raw = llm_client.ask(prompt, system=system, max_tokens=1500)
            llm_answers = json.loads(extract_json_text(raw))
        except Exception:
            llm_answers = {}

    # Merge: learned answers override LLM answers
    answers = {**llm_answers, **known}

    filled = 0
    skipped = 0
    skipped_labels: list[str] = []

    for field in fields:
        label = field.get("label", "")
        value = answers.get(label, "")

        # Skip fields the LLM left blank
        if not str(value).strip():
            skipped += 1
            skipped_labels.append(label)
            continue

        selector = f'[name="{field["name"]}"]' if field.get("name") else f'#{field["id"]}'
        try:
            ftype = field.get("type", "text")
            if ftype in ("text", "email", "tel", "number", "password", "search", "url", "textarea"):
                page.fill(selector, str(value))
                filled += 1
            elif ftype in ("select-one", "select"):
                page.select_option(selector, label=str(value))
                filled += 1
            elif ftype in ("checkbox", "radio"):
                if value and str(value).lower() not in ("false", "no", "0"):
                    page.check(selector)
                filled += 1
            else:
                skipped += 1
                skipped_labels.append(label)
        except Exception:
            skipped += 1
            skipped_labels.append(label)

    return {
        "fields_filled": filled,
        "fields_skipped": skipped,
        "skipped_labels": skipped_labels,
        "answers": answers,
    }
