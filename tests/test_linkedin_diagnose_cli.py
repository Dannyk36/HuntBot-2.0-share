from pathlib import Path

from typer.testing import CliRunner

from huntbot2.cli.main import app


runner = CliRunner()
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def test_linkedin_diagnose_cli_easy_apply():
    html_path = FIXTURES_DIR / "linkedin_logged_in_easy_apply.html"
    result = runner.invoke(
        app,
        ["linkedin-diagnose", "--html-path", str(html_path), "--final-url", "https://www.linkedin.com/jobs/view/123"],
    )

    assert result.exit_code == 0
    assert "login_state=logged_in" in result.stdout
    assert "apply_path=easy_apply" in result.stdout
    assert "signals:" in result.stdout
    assert "easy_apply" in result.stdout


def test_linkedin_diagnose_cli_checkpoint():
    html_path = FIXTURES_DIR / "linkedin_checkpoint.html"
    result = runner.invoke(
        app,
        ["linkedin-diagnose", "--html-path", str(html_path), "--final-url", "https://www.linkedin.com/checkpoint/challenge/"],
    )

    assert result.exit_code == 0
    assert "login_state=checkpoint" in result.stdout
