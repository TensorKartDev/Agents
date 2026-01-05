from agentic.agents.base import Agent, PlanningConfig, PlanningLoop
from agentic.llm.provider import ConsoleEchoProvider
from agentic.memory.simple import ConversationBufferMemory
from agentic.tasks.base import Task


def test_planning_loop_initialization():
    agent = Agent(
        name="test_agent",
        description="A test agent",
        llm_provider=ConsoleEchoProvider(),
        tools={},
        planning=PlanningConfig(max_iterations=3, reflection=False),
        memory=ConversationBufferMemory(),
    )
    task = Task(
        id="test-task",
        agent_name="test_agent",
        description="A test task",
    )
    loop = PlanningLoop(agent=agent, task=task)

    assert loop.agent.name == "test_agent"
    assert loop.task.id == "test-task"
    assert loop.trace == []
"""