"""Accessibility tests — axe-core via Playwright, one page per blueprint.

Run with: pytest -m accessibility
Requires: playwright browsers installed (npx playwright install)
"""

import pytest

# These tests require a live server and playwright — skip if unavailable.
pytest.importorskip("playwright")


@pytest.fixture(scope="module")
def live_server_url():
    """Return the base URL of a running test server.

    In CI this is provided by pytest-flask's live_server fixture.
    Override via LIVE_SERVER_URL env var for manual runs.
    """
    import os

    return os.environ.get("LIVE_SERVER_URL", "http://localhost:5000")


def _run_axe(page) -> list[dict]:
    """Inject axe-core and return violations."""
    page.evaluate(
        """() => {
            return new Promise((resolve, reject) => {
                var s = document.createElement('script');
                s.src = 'https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js';
                s.onload = resolve;
                s.onerror = reject;
                document.head.appendChild(s);
            });
        }"""
    )
    results = page.evaluate("() => axe.run()")
    return results.get("violations", [])


def _assert_no_critical_violations(violations: list[dict]) -> None:
    """Assert no critical or serious axe-core violations."""
    critical = [v for v in violations if v["impact"] in ("critical", "serious")]
    if critical:
        messages = []
        for v in critical:
            nodes = ", ".join(n["html"][:80] for n in v.get("nodes", [])[:3])
            messages.append(f"[{v['impact']}] {v['id']}: {v['description']} — {nodes}")
        pytest.fail("Accessibility violations:\n" + "\n".join(messages))


# One test per blueprint main page
_PAGES = [
    ("/auth/login", "Auth login"),
    ("/", "Dashboard"),
    ("/machines/", "Machines list"),
    ("/events/", "Events list"),
    ("/centers/", "Centers list"),
    ("/tasks/", "Tasks list"),
    ("/meetings/", "Meetings list"),
    ("/documents/", "Documents gallery"),
    ("/members/", "Members list"),
    ("/treasury/", "Treasury list"),
    ("/mailbox/", "Mailbox inbox"),
    ("/mailing/", "Mailing campaigns"),
]


@pytest.mark.accessibility
class TestAccessibility:
    """axe-core accessibility audit for each blueprint's main page."""

    @pytest.mark.parametrize("path,name", _PAGES, ids=[p[1] for p in _PAGES])
    def test_page_has_no_critical_a11y_violations(
        self, live_server_url: str, page, path: str, name: str
    ) -> None:
        """Page {name} passes axe-core critical/serious checks."""
        page.goto(f"{live_server_url}{path}")
        violations = _run_axe(page)
        _assert_no_critical_violations(violations)
