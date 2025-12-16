import traceback
import sys
import pathlib

# Ensure local `src` directory is on sys.path so imports work when running this script directly.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

try:
    from agentic.config import ProjectConfig
    pc = ProjectConfig.from_file('examples/configs/hardware_pen_test.yaml')
    from agentic.agents.orchestrator import Orchestrator
    o = Orchestrator(pc)
    print('Agents:', list(o.agents.keys()))
    print('Tools available:', list(o.tool_registry.available().keys()))
    print('Running...')
    outputs = o.run()
    print('Outputs:')
    for k, v in outputs.items():
        print(f"- {k}: {repr(v)}")
except Exception:
    traceback.print_exc()
