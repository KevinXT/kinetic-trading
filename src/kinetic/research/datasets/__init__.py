"""Research dataset construction.

The builder orchestrates deterministic processing (calendar, alignment, features,
join, validation) into a reproducible research product, then runs the event study
over it. It lives in ``research`` rather than ``processing`` because that last
step is evaluation, and ``processing`` must never depend on ``research``.
"""

from kinetic.research.datasets.builder import DatasetResult, build_dataset

__all__ = ["DatasetResult", "build_dataset"]
