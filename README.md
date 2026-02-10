# AGX Framework v0.2.0

An enterprise-grade framework for building, operating, and scaling intelligent agent workflows. AGX is designed to help teams ship production agents quickly while keeping governance, safety, and extensibility first-class.

**Why teams adopt AGX**
- **Config-first orchestration**: model, tools, tasks, and approvals are defined in YAML so you can iterate without code churn.
- **Enterprise controls**: human-in-the-loop checkpoints, auditable runs, and artifact capture are built in.
- **Tooling flexibility**: plug in internal systems via a registry rather than rewriting planners.
- **Multi-engine support**: run the same workflow with different planners and LLM backends.
- **Real workflows included**: security, sales ops, and edge inference scenarios to accelerate onboarding.

**What it delivers**
- Config-driven orchestration of agents, tasks, and tools.
- Production-ready abstractions for LLM providers and memory backends.
- Extensible tool registry with reusable implementations.
- Practical reference workflows for hardware penetration testing, sales order investigation, and edge/on-board inference.

## Build agents with AGX

AGX is optimized for agent creators. You define intent and capabilities in YAML, plug in tools, and register your agent so teams can run it safely through the shared UI and CLI.

### 1) Create an agent package

Create a new folder under `agents/` with a manifest and a config:

```
agents/<your_agent_slug>/
  agent.yaml
  config.yaml
```

### 2) Define the agent manifest

`agent.yaml` describes your agent to the registry and UI:

```yaml
name: "My Agent"
description: "What this agent does and who it's for."
icon: "icon.svg"          # optional
config_path: "config.yaml"
inputs:
  - name: target_path
    type: string
outputs:
  - name: summary
    type: text
capabilities:
  - "triage"
  - "analysis"
version: "1.0.0"
```

### 3) Define the workflow config

`config.yaml` defines tasks, tools, and approvals:

```yaml
name: "my-agent-workflow"
description: "Short workflow description"
agents:
  my_agent:
    description: "What the agent does"
    tools: [my_tool]
    self_deciding: true
tasks:
  - id: intake
    type: human_input
    agent: my_agent
    description: "Collect required inputs"
    ui:
      title: "Agent Intake"
      fields:
        - id: target_path
          label: "Target path"
          kind: path
          required: true
  - id: analyze
    agent: my_agent
    description: "Analyze the target"
    depends_on: [intake]
    input:
      target_path: "{{inputs.intake.target_path}}"
tools:
  my_tool:
    type: my_package.tools:MyTool
```

### 4) Register your agent

Add your agent slug to the registry so it appears in the UI:

```
agents/agents.yaml
```

```yaml
agents:
  - my_agent
```

### 5) Share it with the group

Once registered, your agent appears in the Admin Web UI and CLI for the entire org. The framework enforces consistent orchestration, approvals, and artifacts while you focus on capability.

## Installation

Installation and environment setup are documented separately:

`FrameworkInstallation.md`

## Project layout

```
.
├── pyproject.toml          # Project metadata and dependencies
├── README.md
├── src/agx
│   ├── __init__.py
│   ├── cli.py              # Typer CLI entrypoint
│   ├── config.py           # YAML-driven agent/task configuration utilities
│   ├── agents
│   │   ├── __init__.py
│   │   ├── base.py         # Core Agent + PlanningLoop definitions
│   │   └── orchestrator.py # Multi-agent multi-task coordinator
│   ├── llm
│   │   ├── __init__.py
│   │   └── provider.py     # LLM provider interfaces and a local echo provider
│   ├── memory
│   │   └── simple.py       # In-memory conversation buffer
│   ├── tasks
│   │   ├── __init__.py
│   │   ├── base.py         # Task dataclasses
│   │   └── runner.py       # Task execution helpers
│   └── tools
│       ├── __init__.py
│       ├── base.py         # Tool protocol definition
│       ├── registry.py     # Registry used by orchestrator and configs
│       └── builtin.py      # Built-in tools for the sample domains
└── examples
    └── configs             # YAML configs per domain
```

## LLM output contract

The default planning loop expects every model turn to return JSON with the following shape:

```json
{
  "thought": "what I'm thinking",
  "action": "tool-name or final",
  "input": "payload sent to the tool or final answer context",
  "answer": "only required when action == \"final\""
}
```

If the output cannot be parsed, the system will treat the entire response as a final answer and stop iterating. This makes it easy to hook up deterministic providers during development yet keeps the prompt structure simple for production models.

### Using Ollama locally

Point a configuration at the built-in `OllamaProvider` to run your self-hosted Mistral, Llama, etc.:

```yaml
defaults:
  llm_provider: agx.llm.provider:OllamaProvider
  llm_params:
    model: llama3
    host: http://127.0.0.1:11434
    options:
      temperature: 0.1
```

Per-agent overrides are supported via `llm_params` blocks inside each agent. The provider automatically sets `stream=false` and surfaces any HTTP/API errors so the orchestrator can stop gracefully.

## Configuration model

Configs provide:

```yaml
name: "hardware-v1"
tasks:
  - id: recon
    description: "Collect fingerprinting data on the target hardware"
    agent: recon_agent
agents:
  recon_agent:
    llm_provider: agx.llm.provider:ConsoleEchoProvider
    tools: [nmap_scan, firmware_diff]
    planning:
      max_iterations: 4
      reflection: true
```

- **tasks** describe what needs to be achieved.
- **agents** define capabilities, linked tools, and planning constraints.
- **tools** (optional) provide additional custom configuration per tool instance.

The CLI resolves the YAML, registers tools, builds agents, and runs the orchestrator.

## Extending the framework

- Create new packages under `src/agx/tools` or an external repository.
- Plug in vector memories, graph planners, streaming observers, etc.
- Deploy via containers and customise configs for your environment.

### Domain-specific scenarios

- **Hardware penetration testing** – reuse `nmap_scan` and `firmware_diff` while adding tools that speak to lab equipment or artifact stores.
- **Sales order investigations** – extend `order_lookup` to query your CRM/ERP and feed structured events into `anomaly_scoring`.
- **Edge/on-board inference** – pair `edge_deployment_planner` with telemetry tools that ingest device stats or OTA reports.
- **Firmware reverse engineering / penetration workflows** – the `firmware_workflow.yaml` config demonstrates the step-by-step diagram provided by the cyber security team (intake, magic-byte detection, carving, Ghidra handoff, secret hunting, weakness profiling, and verification planning). Extend the built-in firmware tools to integrate real decompression, Binwalk runs, or Azure DevOps reporting scripts.

Each scenario boils down to adding new tool factories and swapping configs, so you can keep a single orchestration core across very different domains.

### Firmware tooling integrations (real tools)

The firmware workflow now executes real binaries instead of mocked responses. The tools call `binwalk`, `file`, `readelf`/`objdump`, `strings`, and `ripgrep` directly. To use them:

1. Install dependencies (`binwalk`, `ripgrep`, `binutils` for `readelf`/`objdump`, and `file`).
   - macOS (Homebrew): `brew install binwalk ripgrep binutils`
   - Debian/Ubuntu: `sudo apt-get update && sudo apt-get install -y binwalk ripgrep binutils file`
   - Fedora/RHEL: `sudo dnf install binwalk ripgrep binutils file`
   - Windows: use WSL with the Debian commands above or install the packages via winget/chocolatey where available.
2. Update `examples/configs/firmware_workflow.yaml` to point each task’s `path` at your firmware image (replace `/path/to/firmware.bin`).
3. Set `extract: true` where you want Binwalk carving and adjust `output_dir` to control where artifacts are written.

Outputs include real stdout/stderr from the commands. If a dependency is missing or the path is invalid, the tool surfaces the error instead of fabricating a result.

## Engines & Microsoft Autogen integration

By default `agx run` executes tasks through the Microsoft Agent Framework (Autogen) and calls your Ollama models as the LLM backend. The YAML-defined tools are registered as Autogen functions, so the planner automatically decides when to invoke them. Force an engine explicitly with:

```bash
agx run examples/configs/hardware_pen_test.yaml --engine autogen
```

Switch back to the legacy JSON-contract planner (useful for debugging prompt issues or collecting per-iteration traces) via:

```bash
agx run examples/configs/hardware_pen_test.yaml --engine legacy --show-trace
```

Both engines consume the same configs; the Autogen engine aligns with Microsoft’s latest agent framework guidance.

## Web dashboard

Launch the FastAPI dashboard (serves a static HTML/JS app) to monitor and control runs from a browser:

```bash
uvicorn agx.web.server:app --reload
```

Then open `http://127.0.0.1:8000`:

- Pick a workflow card (or enter a custom config path), choose an engine, and click **Start**. The button toggles to **Stop** while running; click it to cancel the workflow.
- Watch tasks move from pending → thinking → completed with durations, progress, outputs, and a live mission console log.
- “Active workflows” shows in-progress runs with % completion; the UI is mobile-friendly and lives under `src/agx/web/index.html` with supporting assets in the same folder.

Run in production with `uvicorn agx.web.server:app --host 0.0.0.0 --port 8000` or behind your preferred ASGI server/reverse proxy.

### Creating your own agents

- Drop a manifest under `agents/<slug>/agent.yaml` (or `agent.yml`) with `name`, `description`, optional `icon`, and a `config_path` or inline config. Add optional assets/code alongside it.
- Restart the FastAPI server; the Admin Web UI auto-discovers cards from `/api/agents`, so agent creators never touch UI code.
- Run the same config via CLI: `agx run agents/<slug>/agent.yaml --engine autogen` (or `--engine legacy`).
- See `docs/creating_agents.md` for a short, copy-pasteable agent manifest and validation tips.

Manifest metadata such as `inputs`, `outputs`, `capabilities`, `version`, `compatibility`, and `pricing` is supported and validated when agents are discovered. Invalid manifests are skipped with user-friendly errors in the server logs.

## Testing

Use the included `pytest` dependency to verify tools, planners, or integrations:

```bash
pytest
```

The modules are intentionally lightweight to make it easy to adapt the framework to very different agx workloads. Add your own test suites under `tests/` as needed.
