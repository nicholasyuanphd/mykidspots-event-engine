"""Event title blocklist — prevents re-importing rejected events."""

import json
from pathlib import Path

import structlog

log = structlog.get_logger()

DEFAULT_BLOCKLIST_PATH = Path(__file__).parent.parent.parent / "sources" / "blocklist.json"


def _load(path: Path) -> dict:
    if not path.exists():
        return {"blocked_titles": [], "blocked_patterns": []}
    return json.loads(path.read_text())


def is_blocked(title: str, blocklist_path: Path = DEFAULT_BLOCKLIST_PATH) -> bool:
    """Return True if the event title matches a blocklist rule."""
    data = _load(blocklist_path)
    title_lower = title.lower()

    if title in data.get("blocked_titles", []):
        log.debug("event_blocked_by_title", title=title)
        return True

    for pattern in data.get("blocked_patterns", []):
        if pattern.lower() in title_lower:
            log.debug("event_blocked_by_pattern", title=title, pattern=pattern)
            return True

    return False


def is_civic_noise(title: str, blocklist_path: Path = DEFAULT_BLOCKLIST_PATH) -> bool:
    """Return True if the event title is a civic/government administrative event.

    Used for town government sources (CivicPlus) to filter out board meetings,
    hearings, and other non-family content before AI classification.
    """
    data = _load(blocklist_path)
    title_lower = title.lower()
    for pattern in data.get("civic_noise_patterns", []):
        if pattern.lower() in title_lower:
            log.debug("civic_noise_filtered", title=title, pattern=pattern)
            return True
    return False


def add_to_blocklist(
    title: str, blocklist_path: Path = DEFAULT_BLOCKLIST_PATH
) -> None:
    """Add an exact title to the blocklist. Called when curator rejects an event."""
    data = _load(blocklist_path)
    if title not in data["blocked_titles"]:
        data["blocked_titles"].append(title)
        blocklist_path.write_text(json.dumps(data, indent=2))
        log.info("added_to_blocklist", title=title)
