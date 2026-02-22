"""Scraper adapters for event sources."""

from event_engine.scrapers.base import BaseScraper
from event_engine.scrapers.registry import discover_scrapers, get_scraper, register

__all__ = ["BaseScraper", "discover_scrapers", "get_scraper", "register"]
