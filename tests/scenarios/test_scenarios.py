"""Pytest integration for YAML scenario runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.scenarios.runner import ScenarioRunner

SCENARIOS_DIR = Path(__file__).parent


def _find_scenarios() -> list[Path]:
    """Find all YAML scenario files."""
    return sorted(SCENARIOS_DIR.glob("*.yaml"))


@pytest.fixture
def runner() -> ScenarioRunner:
    return ScenarioRunner()


@pytest.mark.parametrize(
    "scenario_file",
    _find_scenarios(),
    ids=[p.stem for p in _find_scenarios()],
)
@pytest.mark.asyncio
async def test_scenario(runner: ScenarioRunner, scenario_file: Path) -> None:
    """Run a YAML scenario and assert all steps pass."""
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
