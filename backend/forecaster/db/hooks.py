"""Keep the optional stores in step with the main database, automatically and best-effort.

    after_outcomes(engine)  — graded forecasts changed → Timescale error series (if on Timescale)
    after_models(engine)    — a model version was created / promoted / rejected / rolled back
                              → MongoDB Atlas model cards (if MONGODB_URI is set)

Neither can fail the operation that triggered it; failures are logged.
"""
from __future__ import annotations

import logging

from forecaster.config import settings

log = logging.getLogger(__name__)


def after_outcomes(engine) -> None:
    from forecaster.db import timescale
    timescale.sync_safe(engine)


def after_models(engine) -> None:
    if not settings.mongodb_uri:
        return
    try:
        from forecaster.db import model_cards
        model_cards.sync(engine)
    except Exception as e:  # noqa: BLE001
        log.warning("MongoDB model-card sync failed: %s", e)


def after_everything(engine) -> None:
    after_outcomes(engine)
    after_models(engine)
