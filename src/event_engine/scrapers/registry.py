"""Scraper registry — auto-discovery and lookup of adapter classes."""

import importlib
import pkgutil

import structlog

from event_engine.scrapers.base import BaseScraper

logger = structlog.get_logger()

# Global registry: platform name → scraper class
_REGISTRY: dict[str, type[BaseScraper]] = {}


def register[T: type[BaseScraper]](cls: T) -> T:
    """Decorator to register a scraper class by its platform name.

    Usage:
        @register
        class LibCalScraper(BaseScraper):
            platform = "libcal"
    """
    platform = cls.platform
    if not platform:
        raise ValueError(f"Scraper {cls.__name__} must define a 'platform' attribute")
    if platform in _REGISTRY:
        raise ValueError(
            f"Platform '{platform}' already registered by {_REGISTRY[platform].__name__}"
        )
    _REGISTRY[platform] = cls
    logger.debug("registered_scraper", platform=platform, cls=cls.__name__)
    return cls


def get_scraper(platform: str) -> type[BaseScraper]:
    """Look up a registered scraper class by platform name."""
    if platform not in _REGISTRY:
        raise KeyError(
            f"No scraper registered for platform '{platform}'. Available: {list(_REGISTRY.keys())}"
        )
    return _REGISTRY[platform]


def discover_scrapers() -> None:
    """Import all scraper modules to trigger @register decorators.

    Scans the event_engine.scrapers package for modules and imports them.
    This causes their @register decorators to fire, populating the registry.
    """
    import event_engine.scrapers as scrapers_pkg

    for module_info in pkgutil.iter_modules(scrapers_pkg.__path__):
        if module_info.name in ("base", "registry"):
            continue
        module_name = f"event_engine.scrapers.{module_info.name}"
        try:
            importlib.import_module(module_name)
            logger.debug("discovered_scraper_module", module=module_name)
        except Exception:
            logger.exception("failed_to_import_scraper", module=module_name)


def registered_platforms() -> list[str]:
    """Return list of all registered platform names."""
    return list(_REGISTRY.keys())
