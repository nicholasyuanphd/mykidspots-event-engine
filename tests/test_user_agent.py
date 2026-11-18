"""The scraper identifies itself with a contact the operator controls."""

from event_engine.orchestrator import DEFAULT_CONTACT, build_user_agent


def test_explicit_contact_is_used() -> None:
    assert build_user_agent("https://kids.example.org") == (
        "MyKidSpots-EventEngine/0.1 (+https://kids.example.org)"
    )


def test_env_contact_is_used(monkeypatch) -> None:
    monkeypatch.setenv("EVENT_ENGINE_CONTACT", "ops@example.org")
    assert build_user_agent().endswith("(+ops@example.org)")


def test_falls_back_to_project_url(monkeypatch) -> None:
    monkeypatch.delenv("EVENT_ENGINE_CONTACT", raising=False)
    assert build_user_agent() == f"MyKidSpots-EventEngine/0.1 (+{DEFAULT_CONTACT})"


def test_blank_contact_falls_back() -> None:
    assert DEFAULT_CONTACT in build_user_agent("   ")
