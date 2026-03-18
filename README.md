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

The packaging and installation contract for AGX-compatible agents is defined in [AAPS.md](/home/administrator/source/Agents/AAPS.md).

### 1) Create an agent package

Create a new folder under `agents/` with a manifest and a config:

```
agents/<your_agent_slug>/
  agent.yaml
  config.yaml
```

That directory layout is the minimum AAPS package root.

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

### Web-based package builder

AGX Admin now supports two package onboarding paths:

- upload a finished `.zip` agent package
- build a package in the browser by uploading agent files or a folder, generating `agent.yaml`, then downloading or installing the package

The package builder expects you to include the workflow config file such as `config.yaml` in the uploaded files. AGX generates `agent.yaml` from the form fields and validates the resulting package before download or install.


## Running AGX with Docker

You can run AGX and its dependencies (Postgres, RabbitMQ) using Docker for easy setup and deployment. The repository provides a `Dockerfile` and a `docker-compose.yml` for this purpose.

### 1. Prepare your environment file

Copy the sample environment file to `.env` in the project root:

```bash
cp src/agx/web/sample.env .env
```

Edit `.env` as needed to set your database credentials, secrets, and bootstrap users. See comments in the file for details.

### 2. Start the containers

Run the following command to build and start AGX, Postgres, and RabbitMQ:

```bash
docker-compose up --build
```

This will launch all services defined in `docker-compose.yml`.

### 3. Access the AGX web dashboard

Once the containers are running, open your browser to:

```
http://localhost:8000
```

You can now use the AGX web UI, register agents, and run workflows.

### 4. Stopping the containers

To stop all running containers:

```bash
docker-compose down
```

---

For more advanced configuration, see `FrameworkInstallation.md` and the comments in `docker-compose.yml`.

---

## Installation

Installation and environment setup are documented separately:

`FrameworkInstallation.md`

For manual or non-containerized deployment, see the instructions above and in the referenced documentation.

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

## Runtime data and log cleanup

AGX currently stores run history in three places:

- `.agx/runs/<run_id>/` for per-run manifests and artifacts created by the web runner.
- `.agx/task_state.db` for local SQLite task state used by the task runners.
- Postgres tables `agx_runs` and `agx_run_events` when `AGX_DB_URL` is configured.

There is no built-in retention or delete command yet. To remove old local run logs, delete the relevant run folders under:

```bash
rm -rf .agx/runs/<run_id>
```

To remove all local run artifacts:

```bash
rm -rf .agx/runs
```

To reset local task state as well:

```bash
rm -f .agx/task_state.db
```

If Postgres persistence is enabled, you must also delete the matching rows from `agx_runs` and `agx_run_events`; removing `.agx/runs` alone does not clear database-backed history.

## Runtime boundary

The runtime should not assume that agent packages, workflows, or their tests live inside the framework source tree.

- The framework code lives under `src/agx`.
- Agent packages and workflow YAML can live in any directory exposed through `AGX_AGENTS_DIR`.
- The agent registry file can live anywhere via `AGX_AGENT_REGISTRY`.
- Run artifacts can be redirected outside the repo via `AGX_RUNS_DIR`.
- Framework tests should validate runtime contracts only; agent-specific workflow tests should live with the agent pack, not in the core runtime suite.

The repo-local `agents/` folder is now just the default workspace convention, not a framework requirement.

For the formal package, install, and security contract behind that separation, see [AAPS.md](/home/administrator/source/Agents/AAPS.md).

## Security and marketplace admin

AGX now supports a lightweight runtime security layer for separate deployment testing:

- Marketplace users authenticate through the admin web surface using a signed session cookie.
- Roles are `developer`, `manager`, and `admin`.
- Admin APIs automatically scope run history and uploaded packages to `owner_user_id` unless the logged-in role is `admin`.
- Agent packages can be uploaded as `.zip` bundles through the admin page and installed into the configured agent-pack root.
- Package metadata and runtime counters track `uploaded_at`, `restart_count`, `traffic_count`, and `last_run_at`.

These behaviors are intended to align with the AAPS managed-install model in [AAPS.md](/home/administrator/source/Agents/AAPS.md).

Recommended bootstrap for a fresh deployment:

- Set `AGX_AUTH_SECRET` to a strong secret.
- Set `AGX_BOOTSTRAP_USERS` to a JSON array with at least one admin user.
- Configure one or more external identity providers so developers use existing accounts instead of AGX-local passwords.
- Optionally move `AGX_AGENTS_DIR`, `AGX_AGENT_REGISTRY`, and `AGX_RUNS_DIR` outside the runtime repo.

Example:

```bash
export AGX_AUTH_SECRET="replace-this"
export AGX_BOOTSTRAP_USERS='[{"tenant_name":"Emerson","tenant_domain":"emerson.com","username":"admin","email":"admin@emerson.com","password":"change-me","role":"admin","display_name":"Marketplace Admin"}]'
export AGX_AGENTS_DIR="/srv/agx/agents"
export AGX_AGENT_REGISTRY="/srv/agx/registry/agents.yaml"
export AGX_RUNS_DIR="/srv/agx/runs"
```

### External login providers

AGX supports external login so agent developers can reuse existing identities:

- Google via FedCM-backed Google Identity Services
- GitHub via OAuth
- Okta via OIDC

The runtime auto-provisions AGX users from the external identity and assigns roles based on email allowlists:

- `AGX_ADMIN_EMAILS`
- `AGX_MANAGER_EMAILS`
- `AGX_ALLOWED_LOGIN_DOMAINS`

Local username/password login remains available as a bootstrap and fallback path for AGX-managed accounts.

Users are persisted in the admin database with:

- tenant membership
- unique email address
- username
- role
- external identity links when Google/GitHub SSO is used

The tenant model is domain-based by default. For example, AGX can maintain a tenant named `Emerson` with domain `emerson.com`, and users such as `ashish.madkaikar@emerson.com` and `nitin.k@emerson.com` will belong to that tenant.

## Architecture

AGX is split into a reusable runtime/framework layer and a set of separately packaged agent definitions inside the same workspace.

### High-level component map

```text
Agentic Workspace
|
+- AGX framework package (src/agx)
|  |
|  +- Config loader
|  |  - parses YAML project configs into ProjectConfig / AgentSpec / TaskSpec
|  |
|  +- Orchestration engines
|  |  - legacy engine: agx.agents.orchestrator.Orchestrator
|  |  - autogen engine: agx.autogen_runner.AutogenOrchestrator
|  |
|  +- Agent runtime
|  |  - agent base classes and planning loop
|  |  - task ordering and execution
|  |  - handoff/binding resolution between tasks
|  |
|  +- Tooling layer
|  |  - Tool protocol
|  |  - ToolRegistry
|  |  - built-in tool implementations
|  |
|  +- LLM and memory abstractions
|  |  - provider interface and implementations
|  |  - conversation memory
|  |
|  +- Runtime integrations
|  |  - RabbitMQ event publishing
|  |  - OpenTelemetry spans/events
|  |
|  +- Persistence and interfaces
|  |  - SQLite task state
|  |  - Postgres run/event store
|  |  - CLI
|  |  - FastAPI web server + WebSocket UI
|  |
|  +- Packaging
|     - Python package metadata and dependencies in pyproject.toml
|
+- Agent packages (agents/*)
|  |
|  +- registry files
|  |  - agents/agents.yaml
|  |  - agents/registry.yaml
|  |
|  +- per-agent package folders
|     - agent.yaml manifest for discovery/UI metadata
|     - config.yaml workflow definition
|     - optional assets/docs such as flow.md or icons
|
+- External systems
   |
   +- LLM backends
   +- RabbitMQ
   +- OpenTelemetry collectors
   +- Postgres
   +- host OS tools invoked by built-in tools
```

### Framework vs agent separation

The separation is real, but not absolute:

- Agents are mostly data packages: manifests in `agent.yaml`, workflows in `config.yaml`, and optional assets.
- The framework discovers those packages through `agents/agents.yaml` or `agents/registry.yaml`, validates manifests, loads configs, and executes them.
- Agent configs depend on framework contracts: task schema, tool names, import-path conventions, task types such as `tool_run` or `agent_handoff`, and runtime features such as approvals and bindings.
- The framework can run without any particular sample agent package. An individual agent package cannot run without the AGX runtime because its config semantics are defined by AGX.
- A stronger separation is possible later by moving agent packs into separate repos or distributable packages, but today they are co-located in one workspace and coupled by configuration/runtime contracts rather than by much custom Python code.

### Workspace configuration

The web runtime resolves workspace paths from environment variables first:

- `AGX_AGENTS_DIR`: root directory containing agent packages
- `AGX_AGENT_REGISTRY`: registry YAML listing discoverable agents
- `AGX_RUNS_DIR`: run artifact root

If unset, AGX falls back to the current repo-local convention for developer convenience.

### Current runtime flow

1. The web UI or CLI selects an agent config file.
2. `ProjectConfig` loads YAML into typed agent/task/tool specs.
3. The selected engine builds the execution graph and resolves dependencies.
4. The runtime registers built-in and configured tools.
5. Tasks execute, optionally pausing for human input or approval.
6. Events stream to the UI, optional middleware, and optional Postgres persistence.
7. Run artifacts are written under `.agx/runs/<run_id>/artifacts`.

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
  middleware:
    rabbitmq_enabled: true
    rabbitmq_url: amqp://guest:guest@localhost:5672/%2F
    rabbitmq_exchange: agx.events
    rabbitmq_routing_prefix: agx
  observability:
    enabled: true
    service_name: agx-framework
```

Per-agent overrides are supported via `llm_params` blocks inside each agent. The provider automatically sets `stream=false` and surfaces any HTTP/API errors so the orchestrator can stop gracefully.

### Running AGX on one machine and GPU inference on another

This deployment pattern is supported:

- AGX Runtime, Web, and Admin can run on machine A.
- Your GPU-backed model server such as Ollama can run on machine B.
- AGX can call the remote model endpoint by setting `defaults.llm_params.host`.
- Agents can also run on registered worker machines through the AGX worker protocol.

#### Recommended working setup

1. Run the AGX web/CLI runtime on the control-plane machine.
2. Run Ollama or another OpenAI-compatible model server on the GPU machine.
3. Point `defaults.llm_params.host` at the GPU machine's reachable HTTP endpoint.
4. Make agent packages available to the AGX machine through one of these methods:
   - copy the package into the AGX host's `AGX_AGENTS_DIR`
   - mount a shared filesystem at `AGX_AGENTS_DIR`
   - upload the package through the AGX admin UI
5. If a tool must use hardware or software that only exists on the second machine, expose that capability as a network service and call it from an AGX tool.

Example config for AGX on `10.0.0.10` and Ollama on GPU host `10.0.0.25`:

```yaml
defaults:
  llm_provider: agx.llm.provider:OllamaProvider
  llm_params:
    model: llama3.2
    host: http://10.0.0.25:11434

agents:
  edge_agent:
    description: Coordinates deployment planning for edge inference workloads
    tools: [edge_deployment_planner, anomaly_scoring]
    planning:
      max_iterations: 4
```

Example environment on the AGX machine:

```bash
export AGX_AGENTS_DIR=/srv/agx/agents
export AGX_AGENT_REGISTRY=/srv/agx/agents/agents.yaml
export AGX_RUNS_DIR=/srv/agx/runs
```

### Desired distributed architecture: AGX control plane plus user-side agent execution

The following topology is now supported as the AGX control-plane plus remote-worker model:

- Host B
  - Ollama
  - RabbitMQ
  - Postgres
- Host A
  - AGX Runtime
  - AGX Web
  - AGX Admin
- User machine
  - AGX CLI
  - agent execution
  - user-local tools used by the agent

#### Deployment topology

```text
                                  +----------------------+
                                  |       Host B         |
                                  |----------------------|
                                  | Ollama / GPU Models  |
                                  | RabbitMQ             |
                                  | Postgres             |
                                  +----------+-----------+
                                             ^
                                             |
                        model calls          | telemetry / persistence
                                             |
+----------------------+          +----------+-----------+
|        Host A        |          |                      |
|----------------------|          |   AGX Control Plane  |
| AGX Runtime          +---------->   /api/run           |
| AGX Web              |          |   /api/worker/*      |
| AGX Admin            |<---------+   WebSocket updates  |
+----------+-----------+          +----------+-----------+
           ^                                 ^
           | browser access                  | worker auth / task lease /
           |                                 | task completion / discovery
           |                                 |
           |                      +----------+-----------+
           |                      |     User Machine     |
           |                      |----------------------|
           |                      | AGX CLI worker       |
           |                      | local agent packs    |
           |                      | local tools/files    |
           |                      +----------------------+
```

Runtime responsibility:

- owns the run record, access control, WebSocket updates, and admin discovery
- leases executable tasks to the selected worker when the agent is worker-backed

Worker responsibility:

- advertises its locally available agents to the runtime
- executes leased tasks locally with access to user-local tools and files
- reports output, logs, and metadata back to the runtime

In this design, the AGX web/runtime tier behaves as the control plane, while the AGX CLI on the user machine behaves as a worker that:

- authenticates to AGX using `/api/worker/login`
- registers itself and its locally available agent packs with `/api/worker/register`
- polls for leased tasks from the runtime via `/api/worker/poll`
- executes the selected task locally so it can access user-local tools and files
- reports task output, console logs, and metadata back through `/api/worker/tasks/{lease_id}/complete`

The runtime remains the system of record for:

- run ownership and access control
- run history and WebSocket updates
- Postgres-backed run persistence
- admin discovery of workers and worker-advertised agents

The admin Discovery page shows:

- the runtime workspace paths in use
- registered worker nodes
- which agents each worker has advertised to the runtime

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
- **defaults.middleware** enables RabbitMQ event publication for run/task/tool events.
- **defaults.observability** enables OpenTelemetry spans/events.

### Cross-agent interoperability

Use binding tokens to pass data between tasks:

```yaml
input:
  previous_summary: "{{results.recon.output}}"
  selected_path: "{{inputs.intake.target_path}}"
```

Use `type: agent_handoff` to create an explicit structured handoff payload between agents:

```yaml
- id: handoff_to_analyst
  type: agent_handoff
  agent: analyst_agent
  source_task: recon
  description: "Pass recon findings to analyst agent"
```

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
