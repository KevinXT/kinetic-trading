"""Task registry duplicate protection."""

import uuid

import pytest
from common.errors import DuplicateTaskError
from pipeline_core.tasks import registry as reg


def test_duplicate_registration_raises() -> None:
    name = f"_registry_test_{uuid.uuid4().hex[:12]}"

    @reg.register(name)
    def _first(ctx, p):  # type: ignore[no-untyped-def]
        pass

    with pytest.raises(DuplicateTaskError, match="already registered"):

        @reg.register(name)
        def _second(ctx, p):  # type: ignore[no-untyped-def]
            pass


def test_allow_override_replaces_task() -> None:
    name = f"_registry_override_{uuid.uuid4().hex[:12]}"

    @reg.register(name, allow_override=True)
    def _a(ctx, p):  # type: ignore[no-untyped-def]
        pass

    @reg.register(name, allow_override=True)
    def _b(ctx, p):  # type: ignore[no-untyped-def]
        pass

    assert reg.TASK_REGISTRY[name] is _b
