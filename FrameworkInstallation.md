# Framework Installation

This document covers environment setup and running the framework locally.

## Install dependencies

```bash
python3 -m venv .venv && source .venv/bin/activate  # or favourite virtualenv tool
pip install --upgrade pip
pip install -e .
```

## Run firmware penetration workflow

```bash
agx run agents/firmware_pen_test/config.yaml --engine autogen
```

For trace-heavy debugging:

```bash
agx run agents/firmware_pen_test/config.yaml --engine legacy --show-trace
```

The workflow invokes firmware-focused tools (preflight, format detection, architecture inference, entropy checks, extraction, OS/magic checks, key discovery, and Ghidra handoff). The CLI defaults to the Autogen engine; use `--engine legacy` if needed.

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
