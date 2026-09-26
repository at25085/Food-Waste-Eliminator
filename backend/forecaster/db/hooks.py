"""Keep derived data in step with the main database, automatically and best-effort.

    after_outcomes(engine)  — graded forecasts changed → Timescale error series (if on Timescale)
    after_models(engine)    — a model version was created / promoted / rejected / rolled back
                              (nothing derived to refresh today; kept as the single place to add it)

Neither can fail the operation that triggered it.
"""
from __future__ import annotations


def after_outcomes(engine) -> None:
    from forecaster.db import timescale
    timescale.sync_safe(engine)


def after_models(engine) -> None:
    return None


def after_everything(engine) -> None:
    after_outcomes(engine)
    after_models(engine)
