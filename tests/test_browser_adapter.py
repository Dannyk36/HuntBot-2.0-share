import builtins

from huntbot2.apply.browser_adapter import probe_job_page


def test_probe_job_page_fallback_when_playwright_unavailable(monkeypatch):
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("playwright"):
            raise ImportError("playwright missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    screenshot_path = "/tmp/probe-shot.png"
    result = probe_job_page("https://example.invalid/job", screenshot_path=screenshot_path)

    assert result["ok"] is False
    assert result["adapter_available"] is False
    assert result["reason"] == "playwright_unavailable"
    assert result["screenshot_path"] == screenshot_path
