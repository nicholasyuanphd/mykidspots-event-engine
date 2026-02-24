"""Run report generation — summarizes scrape results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class SourceResult:
    """Result for a single source scrape."""

    source_id: str
    source_name: str
    events_found: int = 0
    events_normalized: int = 0
    events_inserted: int = 0
    events_updated: int = 0
    events_unchanged: int = 0
    events_errors: int = 0
    duration_ms: int = 0
    status: str = "completed"
    error_message: str | None = None


@dataclass
class RunReport:
    """Summary report for a complete engine run."""

    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    sources: list[SourceResult] = field(default_factory=list)
    dry_run: bool = False

    @property
    def total_found(self) -> int:
        return sum(s.events_found for s in self.sources)

    @property
    def total_inserted(self) -> int:
        return sum(s.events_inserted for s in self.sources)

    @property
    def total_updated(self) -> int:
        return sum(s.events_updated for s in self.sources)

    @property
    def total_errors(self) -> int:
        return sum(s.events_errors for s in self.sources)

    @property
    def sources_succeeded(self) -> int:
        return sum(1 for s in self.sources if s.status == "completed")

    @property
    def sources_failed(self) -> int:
        return sum(1 for s in self.sources if s.status == "failed")

    @property
    def duration_ms(self) -> int:
        if self.finished_at:
            delta = self.finished_at - self.started_at
            return int(delta.total_seconds() * 1000)
        return 0

    def to_markdown(self) -> str:
        """Generate a markdown summary for GitHub Actions step summary."""
        lines = [
            "# Event Engine Run Report",
            "",
            f"**Started:** {self.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        ]

        if self.finished_at:
            lines.append(f"**Finished:** {self.finished_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            lines.append(f"**Duration:** {self.duration_ms / 1000:.1f}s")

        if self.dry_run:
            lines.append("**Mode:** DRY RUN (no DB writes)")

        lines.extend(
            [
                "",
                "## Summary",
                "",
                "| Metric | Count |",
                "|--------|-------|",
                f"| Sources scraped | {len(self.sources)} |",
                f"| Sources succeeded | {self.sources_succeeded} |",
                f"| Sources failed | {self.sources_failed} |",
                f"| Events found | {self.total_found} |",
                f"| Events inserted | {self.total_inserted} |",
                f"| Events updated | {self.total_updated} |",
                f"| Errors | {self.total_errors} |",
                "",
                "## Source Details",
                "",
                "| Source | Found | Inserted | Updated | Errors | Status | Duration |",
                "|--------|-------|----------|---------|--------|--------|----------|",
            ]
        )

        for s in self.sources:
            status_icon = "✅" if s.status == "completed" else "❌"
            lines.append(
                f"| {s.source_name} | {s.events_found} | {s.events_inserted} | "
                f"{s.events_updated} | {s.events_errors} | {status_icon} {s.status} | "
                f"{s.duration_ms / 1000:.1f}s |"
            )

        # Failed sources detail
        failed = [s for s in self.sources if s.status == "failed"]
        if failed:
            lines.extend(["", "## Failures", ""])
            for s in failed:
                lines.append(f"- **{s.source_name}** ({s.source_id}): {s.error_message}")

        return "\n".join(lines)
