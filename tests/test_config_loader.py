from pathlib import Path

from huntbot2.config.loader import load_config


def test_load_config_reads_roles(tmp_path: Path):
    cfg = tmp_path / "profile.yml"
    cfg.write_text(
        "profile:\n  roles:\n    - data analyst\n  locations:\n    - remote\nruntime:\n  default_mode: headed\n  max_retries: 2\n"
    )
    loaded = load_config(cfg)
    assert loaded.profile.roles == ["data analyst"]
    assert loaded.runtime.default_mode == "headed"


def test_load_config_phase1_sections(tmp_path: Path):
    cfg = tmp_path / "profile.yml"
    cfg.write_text(
        """
profile:
  roles: [software engineer]
  locations: [remote]
  remote_only: true
  min_salary: 150000
portals: [linkedin, greenhouse]
filters:
  exclude_keywords: [senior, principal]
  require_sponsorship: false
runtime:
  default_mode: headless
  max_retries: 3
""".strip()
    )

    loaded = load_config(cfg)
    assert loaded.profile.min_salary == 150000
    assert loaded.portals == ["linkedin", "greenhouse"]
    assert loaded.filters.exclude_keywords == ["senior", "principal"]
    assert loaded.filters.require_sponsorship is False
    assert loaded.runtime.default_mode == "headless"
    assert loaded.runtime.max_retries == 3
