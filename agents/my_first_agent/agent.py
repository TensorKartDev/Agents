from agentic.tools.base import Tool, ToolContext, ToolResult

class MyFirstAgentTool(Tool):
    """The main tool for My First Agent."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        return ToolResult(content=f"MyFirstAgentTool received: {input_text}")
