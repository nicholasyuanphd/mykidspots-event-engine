"""AI-powered event classifier using Claude Haiku.

Classifies event titles as yes/no/maybe for kid/family relevance.
Batches 20 titles per API call for cost efficiency.

Cost at current pricing (Claude Haiku 4.5):
  ~$0.000075 per event unbatched → ~$0.000010 per event batched
  At 300K events/month: ~$3/month
"""
from __future__ import annotations

import asyncio
from enum import StrEnum

import structlog
from anthropic import AsyncAnthropic

logger = structlog.get_logger()

BATCH_SIZE = 20

SYSTEM_PROMPT = (
    """You are an event relevance classifier for MyKidSpots, """
    """a platform helping parents discover kid-friendly activities.

Classify each event title as:
- yes: clearly relevant for families with children (ages 0-18)
- no: clearly NOT relevant (board meetings, government hearings, adult-only, professional/business)
- maybe: ambiguous or unclear

Rules:
- Library programs, park activities, school events, family festivals → yes
- Board meetings, hearings, zoning, ordinances, elections, staff meetings → no
- Community events without clear audience → maybe
- Free public events with family potential → maybe (not yes)

Output ONLY one word per line (yes/no/maybe), one per event title, in the same order as input."""
)


class ClassificationResult(StrEnum):
    YES = "yes"
    NO = "no"
    MAYBE = "maybe"


class AIClassifier:
    """Batched Claude Haiku classifier for event kid-relevance."""

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001") -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def classify_batch(self, titles: list[str]) -> list[ClassificationResult]:
        """Classify a batch of event titles.

        Returns one ClassificationResult per title, in the same order.
        Falls back to MAYBE for all titles if the API call fails.
        """
        if not titles:
            return []

        try:
            numbered = "\n".join(f"{i + 1}. {title}" for i, title in enumerate(titles))
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=BATCH_SIZE * 4,  # ~3 chars per response + newline
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": numbered}],
            )

            raw = response.content[0].text.strip()
            lines = [line.strip().lower() for line in raw.splitlines() if line.strip()]

            results: list[ClassificationResult] = []
            for line in lines[:len(titles)]:
                # Extract first word in case model adds brief explanation
                word = line.split()[0] if line.split() else "maybe"
                if word == "yes":
                    results.append(ClassificationResult.YES)
                elif word == "no":
                    results.append(ClassificationResult.NO)
                else:
                    results.append(ClassificationResult.MAYBE)

            # Pad with MAYBE if model returned fewer lines than expected
            while len(results) < len(titles):
                results.append(ClassificationResult.MAYBE)

            logger.info(
                "batch_classified",
                total=len(titles),
                yes=results.count(ClassificationResult.YES),
                no=results.count(ClassificationResult.NO),
                maybe=results.count(ClassificationResult.MAYBE),
            )
            return results

        except Exception:
            logger.exception("ai_classification_failed_falling_back_to_maybe", count=len(titles))
            return [ClassificationResult.MAYBE] * len(titles)

    async def classify_all(self, titles: list[str]) -> list[ClassificationResult]:
        """Classify any number of titles, batching in groups of BATCH_SIZE."""
        if not titles:
            return []

        # Process batches concurrently (max 3 parallel calls)
        semaphore = asyncio.Semaphore(3)
        batches = [titles[i : i + BATCH_SIZE] for i in range(0, len(titles), BATCH_SIZE)]

        async def _classify_with_semaphore(batch: list[str]) -> list[ClassificationResult]:
            async with semaphore:
                return await self.classify_batch(batch)

        batch_results = await asyncio.gather(*[_classify_with_semaphore(b) for b in batches])

        # Flatten
        return [result for batch in batch_results for result in batch]
