from agentic.agents.orchestrator import Orchestrator
from agentic.config import ProjectConfig


def test_orchestrator_initialization():
    config_yaml = """
name: test-project
agents:
  test_agent:
    description: "A test agent"
tasks:
  - id: test-task
    agent: test_agent
    description: "A test task"
"""
    config = ProjectConfig.from_yaml(config_yaml)
    orchestrator = Orchestrator(config)

    assert orchestrator.config.name == "test-project"
    assert "test_agent" in orchestrator.agents
    assert len(orchestrator.tasks) == 1
    assert orchestrator.tasks[0].id == "test-task"
"""