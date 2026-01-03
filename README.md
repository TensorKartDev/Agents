# Agentic Template Starter

A batteries-included template for building agentic systems that can be copied into new projects. It focuses on:

- Config-driven orchestration of agents, tasks, and tools.
- Simple abstractions for LLM providers and memory backends.
- Extensible tool registry with reusable implementations.
- Ready-to-run examples for hardware penetration testing, sales order investigation, and edge/on-board inference.

## Project layout

```
.
├── pyproject.toml          # Project metadata and dependencies
├── README.md
├── src/agentic
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

## Quick start

1. **Install dependencies**
   ```bash
   python3 -m venv .venv && source .venv/bin/activate  # or favourite virtualenv tool
   pip install --upgrade pip
   pip install -e .
   ```

2. **Run an example scenario**
   ```bash
   agentic run examples/configs/hardware_pen_test.yaml
   ```
   Replace the config path with `sales_order_investigation.yaml` or `edge_inference.yaml` for the other domains.
   For the firmware penetration workflow delivered by the security team use:
   ```bash
   agentic run examples/configs/firmware_workflow.yaml --show-trace
   ```
   Each turn follows the JSON contract shown below so the agent can invoke tools such as `firmware_intake`, `firmware_format_identifier`, and `weakness_profiler` that encode the team’s process. The CLI defaults to the Microsoft Autogen/MAF engine, which drives Ollama-hosted models. Pass `--engine legacy` if you need the original in-house loop.

3. **Swap in your LLM provider**
   - Implement `LLMProvider` (see `src/agentic/llm/provider.py`).
   - Reference it in config by module path, or inject programmatically before running tasks.

4. **Add tools**
   - Subclass `Tool` from `src/agentic/tools/base.py`.
   - Register with `ToolRegistry` or list it in the YAML config to wire it to an agent.

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
  llm_provider: agentic.llm.provider:OllamaProvider
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
    llm_provider: agentic.llm.provider:ConsoleEchoProvider
    tools: [nmap_scan, firmware_diff]
    planning:
      max_iterations: 4
      reflection: true
```

- **tasks** describe what needs to be achieved.
- **agents** define capabilities, linked tools, and planning constraints.
- **tools** (optional) provide additional custom configuration per tool instance.

The CLI resolves the YAML, registers tools, builds agents, and runs the orchestrator.

## Extending the template

- Create new packages under `src/agentic/tools` or an external repository.
- Plug in vector memories, graph planners, streaming observers, etc.
- Deploy via containers by copying this template and customising configs.

### Domain-specific starter ideas

- **Hardware penetration testing** – reuse `nmap_scan` and `firmware_diff` while adding tools that speak to lab equipment or artifact stores.
- **Sales order investigations** – extend `order_lookup` to query your CRM/ERP and feed structured events into `anomaly_scoring`.
- **Edge/on-board inference** – pair `edge_deployment_planner` with telemetry tools that ingest device stats or OTA reports.
- **Firmware reverse engineering / penetration workflows** – the `firmware_workflow.yaml` config demonstrates the step-by-step diagram provided by the cyber team (intake, magic-byte detection, carving, Ghidra handoff, secret hunting, weakness profiling, and verification planning). Extend the built-in firmware tools to integrate real decompression, Binwalk runs, or Azure DevOps reporting scripts.

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

By default `agentic run` executes tasks through the Microsoft Agent Framework (Autogen) and calls your Ollama models as the LLM backend. The YAML-defined tools are registered as Autogen functions, so the planner automatically decides when to invoke them. Force an engine explicitly with:

```bash
agentic run examples/configs/hardware_pen_test.yaml --engine autogen
```

Switch back to the legacy JSON-contract planner (useful for debugging prompt issues or collecting per-iteration traces) via:

```bash
agentic run examples/configs/hardware_pen_test.yaml --engine legacy --show-trace
```

Both engines consume the same configs; the Autogen engine aligns with Microsoft’s latest agent framework guidance.

## Web dashboard

Launch the FastAPI dashboard (serves a static HTML/JS app) to monitor and control runs from a browser:

```bash
uvicorn agentic.web.server:app --reload
```

Then open `http://127.0.0.1:8000`:

- Pick a workflow card (or enter a custom config path), choose an engine, and click **Start**. The button toggles to **Stop** while running; click it to cancel the workflow.
- Watch tasks move from pending → thinking → completed with durations, progress, outputs, and a live mission console log.
- “Active workflows” shows in-progress runs with % completion; the UI is mobile-friendly and lives under `src/agentic/web/index.html` with supporting assets in the same folder.

Run in production with `uvicorn agentic.web.server:app --host 0.0.0.0 --port 8000` or behind your preferred ASGI server/reverse proxy.

## Testing

Use the included `pytest` dependency to verify tools, planners, or integrations:

```bash
pytest
```

The sample modules are intentionally lightweight to make it easy to adapt the template to very different agentic workloads.
