# Canonical local/CI validation and release-hygiene targets.
# Keep command bodies aligned with scripts/dev/* and .github/workflows/ci.yml.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

SCRIPTS := scripts/dev

.PHONY: help lint format-check typecheck test check-build-pollution clean-build \
	wheels-smoke validate check-dirty-tree source-archive release-check

help:
	@echo "Validation (dirty trees allowed):"
	@echo "  make lint                 ruff check ."
	@echo "  make format-check         scoped black --check (CI scope)"
	@echo "  make typecheck            scoped mypy (CI scope)"
	@echo "  make test                 pytest -q"
	@echo "  make check-build-pollution  fail if packages/*/build or apps/*/build exist"
	@echo "  make clean-build          remove package/app build artifacts"
	@echo "  make wheels-smoke         build wheels + isolated import smoke"
	@echo "  make validate             pollution check + lint/format/type/test + wheels-smoke"
	@echo
	@echo "Release (clean tree required unless ALLOW_DIRTY_TREE=1):"
	@echo "  make check-dirty-tree     fail when git status is dirty"
	@echo "  make source-archive       git archive + SHA-256 under dist/"
	@echo "  make release-check        dirty-tree gate + validate + source-archive"

lint:
	ruff check .

format-check:
	$(SCRIPTS)/format_check.sh

typecheck:
	$(SCRIPTS)/typecheck.sh

test:
	pytest -q

check-build-pollution:
	$(SCRIPTS)/check_build_pollution.sh

clean-build:
	$(SCRIPTS)/clean_build.sh

wheels-smoke:
	$(SCRIPTS)/wheels_smoke.sh

# Development validation. Dirty trees are allowed; stale build/ trees are not.
validate: check-build-pollution lint format-check typecheck test wheels-smoke check-build-pollution

check-dirty-tree:
	$(SCRIPTS)/check_dirty_tree.sh

source-archive:
	$(SCRIPTS)/source_archive.sh

release-check: check-dirty-tree validate source-archive
