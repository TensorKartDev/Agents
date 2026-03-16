# Framework Installation

This document covers environment setup and running the framework locally.

## Install dependencies

```bash
python3 -m venv .venv && source .venv/bin/activate  # or favourite virtualenv tool
pip install --upgrade pip
pip install -e .
```

## Run with Docker

The repo now includes:

- [Dockerfile](/home/administrator/source/Agents/Dockerfile) for the AGX runtime
- [docker-compose.yml](/home/administrator/source/Agents/docker-compose.yml) for AGX + Postgres + RabbitMQ
- [.env.docker.example](/home/administrator/source/Agents/.env.docker.example) for compose-time secrets/bootstrap users

Start the full stack:

```bash
cp .env.docker.example .env
docker compose up --build
```

Endpoints:

- AGX web UI: `http://localhost:8000`
- RabbitMQ management: `http://localhost:15672`
- Postgres: `localhost:5432`

Compose wiring:

- AGX uses `postgres` as the Postgres host
- AGX uses `rabbitmq` as the RabbitMQ host
- agent packages are mounted from the repo `agents/` directory into `/data/agents`
- run artifacts and admin DB are stored in Docker volumes

If you already have an existing virtualenv such as `agentenv`, rerun:

```bash
pip install -e .
```

This is required after dependency changes such as the web auth/session stack adding `itsdangerous`, `authlib`, or `python-multipart`.

Google FedCM sign-in also requires the backend token-verification dependency now included in the project:

```bash
pip install -e .
```

For Google sign-in to work in a browser, configure:

- `AGX_GOOGLE_CLIENT_ID` for FedCM-enabled sign-in
- `AGX_GOOGLE_CLIENT_SECRET` if you also want OAuth redirect fallback for browsers without FedCM

And register your AGX origin in the Google Cloud OAuth client configuration, for example:

- Authorized JavaScript origin: `http://localhost:8000`
- Redirect URI for legacy fallback: `http://localhost:8000/auth/oauth/google/callback`

## Run an example scenario

```bash
agx run examples/configs/hardware_pen_test.yaml
```

Replace the config path with `sales_order_investigation.yaml` or `edge_inference.yaml` for the other domains.
For the firmware penetration workflow delivered by the security team use:

```bash
agx run examples/configs/firmware_workflow.yaml --show-trace
```

Each turn follows the JSON contract shown below so the agent can invoke tools such as `firmware_intake`, `firmware_format_identifier`, and `weakness_profiler` that encode the team’s process. The CLI defaults to the Microsoft Autogen/MAF engine, which drives Ollama-hosted models. Pass `--engine legacy` if you need the original in-house loop.

## Configure Postgres run store

Set the database URL to persist runs and audit events:

```bash
export AGX_DB_URL="postgresql+psycopg://admin:@localhost:5432/agx"
```

## Swap in your LLM provider

- Implement `LLMProvider` (see `src/agx/llm/provider.py`).
- Reference it in config by module path, or inject programmatically before running tasks.

## Add tools

- Subclass `Tool` from `src/agx/tools/base.py`.
- Register with `ToolRegistry` or list it in the YAML config to wire it to an agent.
