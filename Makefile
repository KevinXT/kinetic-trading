# Canonical local/CI validation and release-hygiene targets.
# Keep command bodies aligned with scripts/dev/* and .github/workflows/ci.yml.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

SCRIPTS := scripts/dev

.PHONY: help install lint lint-imports format-check typecheck test deps-check \
	check-build-pollution clean-build wheel-smoke validate check-dirty-tree \
	source-archive release-check demo

help:
	@echo "Setup:"
	@echo "  make install              uv sync --all-extras --dev"
	@echo
	@echo "Validation (dirty trees allowed):"
	@echo "  make lint                 ruff check ."
	@echo "  make lint-imports         import-linter package boundary contracts"
	@echo "  make format-check         black --check src tests tools projects"
	@echo "  make typecheck            mypy src"
	@echo "  make test                 pytest -q"
	@echo "  make deps-check           deptry: unused / missing / transitive deps"
	@echo "  make check-build-pollution  fail if a stale build/ tree exists"
	@echo "  make clean-build          remove build artifacts"
	@echo "  make wheel-smoke          build the wheel + isolated import/CLI smoke"
	@echo "  make validate             everything above, in order"
	@echo
	@echo "Try it:"
	@echo "  make demo                 run the offline research demo pipeline"
	@echo
	@echo "Release (clean tree required unless ALLOW_DIRTY_TREE=1):"
	@echo "  make check-dirty-tree     fail when git status is dirty"
	@echo "  make source-archive       git archive + SHA-256 under dist/"
	@echo "  make release-check        dirty-tree gate + validate + source-archive"

install:
	uv sync --all-extras --dev

lint:
	ruff check .

lint-imports:
	lint-imports

format-check:
	$(SCRIPTS)/format_check.sh

typecheck:
	$(SCRIPTS)/typecheck.sh

test:
	pytest -q

deps-check:
	deptry .

check-build-pollution:
	$(SCRIPTS)/check_build_pollution.sh

clean-build:
	$(SCRIPTS)/clean_build.sh

wheel-smoke:
	$(SCRIPTS)/wheel_smoke.sh

demo:
	kinetic run configs/research/news_market_dataset_demo.yaml --run-id demo

# Development validation. Dirty trees are allowed; stale build/ trees are not.
validate: check-build-pollution lint lint-imports format-check typecheck deps-check test wheel-smoke check-build-pollution

check-dirty-tree:
	$(SCRIPTS)/check_dirty_tree.sh

source-archive:
	$(SCRIPTS)/source_archive.sh

release-check: check-dirty-tree validate source-archive
