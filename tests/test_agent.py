from agentic.agents.base import Agent, PlanningConfig
from agentic.llm.provider import ConsoleEchoProvider
from agentic.memory.simple import ConversationBufferMemory


def test_agent_initialization():
    agent = Agent(
        name="test_agent",
        description="A test agent",
        llm_provider=ConsoleEchoProvider(),
        tools={},
        planning=PlanningConfig(max_iterations=3, reflection=False),
        memory=ConversationBufferMemory(),
    )

    assert agent.name == "test_agent"
    assert agent.description == "A test agent"
    assert isinstance(agent.llm_provider, ConsoleEchoProvider)
    assert agent.tools == {}
    assert agent.planning.max_iterations == 3
    assert isinstance(agent.memory, ConversationBufferMemory)
"""