"""The LLM tailoring prompts must include the job description so the resume and
cover letter are tailored to the role's actual requirements, not just its title."""
from huntbot2.tailoring.cover import generate_cover_note_llm
from huntbot2.tailoring.resume import generate_resume_variant_llm


class _CapturingLLM:
    def __init__(self):
        self.prompt = None
        self.system = None

    def ask(self, prompt, *, system=None, max_tokens=1024):
        self.prompt = prompt
        self.system = system
        return "OUTPUT"


def test_resume_prompt_includes_description_and_keywords():
    llm = _CapturingLLM()
    job = {
        "title": "Operations Manager",
        "company": "Acme",
        "location": "Phoenix, AZ",
        "description": "Lead warehouse logistics and supply chain KPIs.",
        "score_keywords": ["logistics", "supply chain"],
    }
    generate_resume_variant_llm("# Resume\n- did things", job, llm)
    assert "Lead warehouse logistics" in llm.prompt
    assert "logistics" in llm.prompt
    assert "ATS" in llm.system  # ATS-aware system prompt


def test_resume_prompt_works_without_description():
    llm = _CapturingLLM()
    generate_resume_variant_llm("# Resume", {"title": "Analyst"}, llm)
    assert "Analyst" in llm.prompt


def test_cover_prompt_includes_description_and_no_hardcoded_city(tmp_path):
    llm = _CapturingLLM()
    job = {
        "title": "Ops Manager",
        "company": "Acme",
        "location": "Austin, TX",
        "description": "Own end-to-end fulfillment operations.",
    }
    generate_cover_note_llm(job, ["operations"], llm, project_dir=tmp_path)
    assert "end-to-end fulfillment" in llm.prompt
    assert "Austin, TX" in llm.prompt
    assert "Phoenix, AZ" not in llm.prompt  # no hardcoded fallback city
