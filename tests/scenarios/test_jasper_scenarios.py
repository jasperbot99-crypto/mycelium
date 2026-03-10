"""Pytest integration for Jasper real-world incident scenarios.

Discovers YAML scenario files from data/jasper/ and runs them through
the standard ScenarioRunner with in-memory repositories.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.scenarios.runner import ScenarioRunner

JASPER_DIR = Path(__file__).parent / "data" / "jasper"


def _find_jasper_scenarios() -> list[Path]:
    """Find all Jasper incident YAML scenario files."""
    if not JASPER_DIR.is_dir():
        return []
    return sorted(JASPER_DIR.glob("*.yaml"))


@pytest.fixture
def runner() -> ScenarioRunner:
    return ScenarioRunner()


@pytest.mark.parametrize(
    "scenario_file",
    _find_jasper_scenarios(),
    ids=[p.stem for p in _find_jasper_scenarios()],
)
@pytest.mark.asyncio
async def test_jasper_scenario(runner: ScenarioRunner, scenario_file: Path) -> None:
    """Run a Jasper incident scenario and assert all steps pass."""
    result = await runner.run_file(scenario_file)

    if not result.passed:
        failures = [
            f"  Step {s.step_index} ({s.action}): {s.message}"
            for s in result.steps
            if not s.passed
        ]
        error_msg = f"Scenario '{result.name}' failed:\n" + "\n".join(failures)
        if result.error:
            error_msg += f"\n  Error: {result.error}"
        pytest.fail(error_msg)
