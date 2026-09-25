"""Contracts for the runtime mechanism and application composition split."""

from __future__ import annotations

import inspect

from src.app import composition
from src.app.core import runtime, setup


def test_runtime_mechanism_is_owned_by_core_runtime() -> None:
    assert setup.StartupPhase is runtime.StartupPhase
    assert setup.compose_phases is runtime.compose_phases


def test_core_setup_exposes_only_reusable_runtime_hooks() -> None:
    setup_source = inspect.getsource(setup)
    assert "create_application" not in setup_source
    assert "lifespan_factory" not in setup_source
    assert "lifespan_with_admin" not in setup_source
    assert "src.app.api" not in setup_source
    assert "src.app.modules" not in setup_source


def test_application_composition_owns_the_web_phase_plan() -> None:
    composition_source = inspect.getsource(composition)
    assert "def lifespan_factory" in composition_source
    assert "def lifespan_with_admin" in composition_source
    assert "StartupPhase(\"users_lookups\"" in composition_source
    assert "StartupPhase(\"admin\"" in composition_source
    assert "compose_phases" in composition_source
