"""Maps namespaced task identifiers to callables.

There is deliberately **no module-level registry** and **no registration
decorator**. Importing a module must never change what the application can do.
The registry is an object, built once by :mod:`kinetic.bootstrap` and handed to
the runner:

    registry = build_default_registry()
    run_pipeline(config, registry=registry)

Task identifiers are namespaced by subsystem and provider, e.g.
``market.alpaca.fetch_bars``, ``news.gdelt.fetch_articles``,
``research.build_news_market_dataset``. The namespace is what makes an unknown
task name actionable: the error tells you which family it should have been in.
"""

from __future__ import annotations

import re
import warnings
from typing import Dict, Iterator, Mapping

from kinetic.core.errors import DuplicateTaskError, PipelineError
from kinetic.core.pipeline.task import TaskFn

# A task id is dot-separated lowercase segments: `news.gdelt.fetch_articles`.
_TASK_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


def validate_task_id(task_id: str) -> str:
    """Return the normalized task id, or raise ``PipelineError``."""
    normalized = task_id.strip()
    if not _TASK_ID_RE.match(normalized):
        raise PipelineError(
            f"Invalid task identifier {task_id!r}. Task ids are dot-separated "
            "lowercase segments, for example 'news.gdelt.fetch_articles'."
        )
    return normalized


class TaskRegistry:
    """An explicit, mutable-at-build-time mapping of task id to callable."""

    def __init__(self, tasks: Mapping[str, TaskFn] | None = None) -> None:
        self._tasks: Dict[str, TaskFn] = {}
        self._aliases: Dict[str, str] = {}
        self._alias_removal: Dict[str, str] = {}
        for name, fn in (tasks or {}).items():
            self.register(name, fn)

    def register(self, task_id: str, fn: TaskFn, *, allow_override: bool = False) -> None:
        """Add a task.

        Duplicate ids raise :class:`DuplicateTaskError` so an accidental
        double-registration during composition is loud rather than order-dependent.
        """
        normalized = validate_task_id(task_id)
        if normalized in self._tasks and not allow_override:
            raise DuplicateTaskError(
                f"Task {normalized!r} is already registered. Pass allow_override=True "
                "if replacing it is intentional."
            )
        self._tasks[normalized] = fn

    def register_alias(self, alias: str, task_id: str, *, removal_version: str = "") -> None:
        """Point a deprecated identifier at a current one.

        Resolving an alias emits a :class:`DeprecationWarning` naming the current
        id, so a stale config tells you exactly what to change. Aliases are never
        returned by :meth:`task_ids`, so ``kinetic task list`` shows the real
        interface rather than the history.
        """
        if task_id not in self._tasks:
            raise PipelineError(f"Cannot alias {alias!r} to unknown task {task_id!r}.")
        if alias in self._tasks:
            raise DuplicateTaskError(f"Alias {alias!r} collides with a registered task.")
        self._aliases[alias] = task_id
        if removal_version:
            self._alias_removal[alias] = removal_version

    def resolve(self, task_id: str) -> TaskFn:
        """Look up a task, raising ``PipelineError`` with context when missing."""
        if task_id in self._tasks:
            return self._tasks[task_id]
        target = self._aliases.get(task_id)
        if target is not None:
            removal = self._alias_removal.get(task_id)
            horizon = f" It is removed in kinetic {removal}." if removal else ""
            warnings.warn(
                f"Task name {task_id!r} is deprecated; use {target!r}.{horizon}",
                DeprecationWarning,
                stacklevel=2,
            )
            return self._tasks[target]
        raise PipelineError(self._unknown_task_message(task_id))

    def canonical_id(self, task_id: str) -> str:
        """Return the current id for ``task_id``, following an alias if needed."""
        return self._aliases.get(task_id, task_id)

    def alias_target(self, alias: str) -> str | None:
        """Return the task an alias points at, or ``None`` if it is not an alias."""
        return self._aliases.get(alias)

    def _unknown_task_message(self, task_id: str) -> str:
        namespace = task_id.split(".")[0] if "." in task_id else ""
        siblings = sorted(t for t in self._tasks if t.startswith(f"{namespace}."))
        if siblings:
            listed = ", ".join(siblings)
            return (
                f"No task registered for {task_id!r}. Tasks in the {namespace!r} "
                f"namespace: {listed}."
            )
        namespaces = sorted({t.split(".")[0] for t in self._tasks})
        return (
            f"No task registered for {task_id!r}. Known namespaces: "
            f"{', '.join(namespaces) or 'none'}. Run 'kinetic task list' to see every task."
        )

    def task_ids(self) -> list[str]:
        """Every registered task id, sorted. Aliases are excluded."""
        return sorted(self._tasks)

    def deprecated_aliases(self) -> dict[str, str]:
        """Every alias, mapped to the task it resolves to."""
        return dict(self._aliases)

    def __contains__(self, task_id: object) -> bool:
        return task_id in self._tasks or task_id in self._aliases

    def __iter__(self) -> Iterator[str]:
        return iter(self.task_ids())

    def __len__(self) -> int:
        return len(self._tasks)
