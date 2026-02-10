# Framework Installation

This document covers environment setup and running the framework locally.

## Install dependencies

```bash
python3 -m venv .venv && source .venv/bin/activate  # or favourite virtualenv tool
pip install --upgrade pip
pip install -e .
```

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

## Swap in your LLM provider

- Implement `LLMProvider` (see `src/agx/llm/provider.py`).
- Reference it in config by module path, or inject programmatically before running tasks.

## Add tools

- Subclass `Tool` from `src/agx/tools/base.py`.
- Register with `ToolRegistry` or list it in the YAML config to wire it to an agent.
