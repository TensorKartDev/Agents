"""Command line interface for the agentic template."""

from __future__ import annotations

from pathlib import Path
import sys

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from .agents.orchestrator import Orchestrator
from .autogen_runner import AutogenOrchestrator
from .config import ProjectConfig
from .tasks.base import TaskState
from .tasks.runner import TaskRunner

app = typer.Typer(help="Agentic template CLI")
console = Console()


def _render_plan(config: ProjectConfig) -> None:
    plan = Table(title="Execution Plan", show_lines=True)
    plan.add_column("Task ID")
    plan.add_column("Agent")
    plan.add_column("Description")
    for spec in config.tasks:
        plan.add_row(spec.id, spec.agent, spec.description)
    console.print(plan)


@app.command()
def run(
    config_path: Path = typer.Argument(..., help="Path to YAML configuration"),
    show_trace: bool = False,
    engine: str = typer.Option("autogen", help="Engine to use: autogen or legacy"),
) -> None:
    """Execute the tasks described in the given config file."""

    config = ProjectConfig.from_file(str(config_path))
    console.print(f"[bold green]Running project[/] {config.name} (engine={engine})")
    _render_plan(config)

    task_specs = list(config.tasks)
    results: dict[str, str] = {}
    progress = Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[progress.description]{task.description}"),
        TextColumn("{task.fields[status]}"),
        transient=False,
    )

    interactive = sys.stdin.isatty()

    def approval_prompt(task) -> bool:
        reason = getattr(task, "reason", "") or "Approval required."
        console.print(f"[yellow]Human approval required[/]: {reason}")
        return typer.confirm("Approve and continue?", default=False)

    approval_callback = approval_prompt if interactive else None

    if engine == "legacy":
        orchestrator = Orchestrator(config)
        orchestrator.runner._approval_callback = approval_callback
    else:
        orchestrator = AutogenOrchestrator(config, approval_callback=approval_callback)

    with progress:
        progress_tasks = {
            task_spec.id: progress.add_task(
                f"{task_spec.id} - {task_spec.description}", status="[yellow]pending", start=False
            )
            for task_spec in task_specs
        }
        if engine == "legacy":
            for task_spec in task_specs:
                progress.update(progress_tasks[task_spec.id], status="[cyan]queued", start=True)
            run_results = orchestrator.runner.run_all(orchestrator.tasks)
            for task_id, result in run_results.items():
                results[task_id] = result.output
                status = "[green]completed ✅" if result.state == TaskState.COMPLETED else "[red]failed"
                if result.state == TaskState.WAITING_HUMAN:
                    status = "[yellow]waiting for approval"
                progress.update(progress_tasks[task_id], status=status)
        else:
            for task_spec in task_specs:
                progress.update(progress_tasks[task_spec.id], status="[cyan]queued", start=True)
            results = orchestrator.run()
            for task_id, output in results.items():
                status = "[green]completed ✅"
                if isinstance(output, str) and output.startswith("WAITING_HUMAN"):
                    status = "[yellow]waiting for approval"
                progress.update(progress_tasks[task_id], status=status)

    table = Table(title="Task outputs", show_lines=True)
    table.add_column("Task ID")
    table.add_column("Output")
    for task_id, output in results.items():
        table.add_row(task_id, output)
    console.print(table)

    if show_trace and engine == "legacy":
        for task in orchestrator.tasks:
            result = orchestrator.runner.results().get(task.id)
            if not result:
                continue
            console.rule(f"Trace for {task.id}")
            for entry in result.trace:
                console.print(entry)
    elif show_trace:
        console.print("[yellow]Trace output is only available for the legacy engine.[/]")


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
