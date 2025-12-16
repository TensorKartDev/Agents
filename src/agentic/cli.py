"""Command line interface for the agentic template."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .agents.orchestrator import Orchestrator
from .config import ProjectConfig

app = typer.Typer(help="Agentic template CLI")
console = Console()


@app.command()
def run(config_path: Path = typer.Argument(..., help="Path to YAML configuration"), show_trace: bool = False) -> None:
    """Execute the tasks described in the given config file."""

    config = ProjectConfig.from_file(str(config_path))
    orchestrator = Orchestrator(config)
    console.print(f"[bold green]Running project[/] {config.name}")
    results = orchestrator.run()
    table = Table(title="Task outputs", show_lines=True)
    table.add_column("Task ID")
    table.add_column("Output")
    for task_id, output in results.items():
        table.add_row(task_id, output)
    console.print(table)

    if show_trace:
        for task in orchestrator.tasks:
            result = orchestrator.runner.results().get(task.id)
            if not result:
                continue
            console.rule(f"Trace for {task.id}")
            for entry in result.trace:
                console.print(entry)


@app.command()
def inspect(config_path: Path = typer.Argument(..., help="Config to inspect")) -> None:
    """Print the agents, tasks, and tools defined by a configuration file."""

    config = ProjectConfig.from_file(str(config_path))
    orchestrator = Orchestrator(config)
    console.print(f"[bold]Project:[/] {config.name}\n{config.description or ''}")
    console.print("[bold]Agents[/]")
    for agent in orchestrator.agents.values():
        console.print(f"- {agent.name}: tools={[name for name in agent.tools.keys()]}")
    console.print("[bold]Tasks[/]")
    for task in orchestrator.tasks:
        console.print(f"- {task.id} -> {task.agent_name}: {task.description}")


if __name__ == "__main__":  # pragma: no cover
    app()
