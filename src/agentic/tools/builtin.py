"""Built-in tools that demonstrate different domains."""

from __future__ import annotations

import difflib
import json
from typing import Any, Dict, Iterable, List, Sequence

import yaml

from .base import Tool, ToolContext, ToolResult
from .registry import ToolRegistry


def _load_structured(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError:
            return text


class NmapScanTool(Tool):
    """Simulated nmap scan that returns surface insights for hardware."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        target = input_text or self.config.get("target", "device.local")
        open_ports = self.config.get("open_ports", [22, 80, 443])
        firmware = self.config.get("firmware", "unknown")
        report = (
            f"Scan summary for {target}: open ports {open_ports}. "
            f"Firmware fingerprint {firmware}. Recommend following up on SSH hardening."
        )
        metadata = {"target": str(target)}
        return ToolResult(content=report, metadata=metadata)


class FirmwareDiffTool(Tool):
    """Produces a textual diff between two firmware manifests."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text)
        if not isinstance(payload, dict):
            raise ValueError("FirmwareDiffTool expects a JSON/YAML payload with 'baseline' and 'current'")
        baseline = (payload.get("baseline") or "").splitlines()
        current = (payload.get("current") or "").splitlines()
        diff = "\n".join(
            difflib.unified_diff(baseline, current, fromfile="baseline", tofile="current", lineterm="")
        )
        if not diff:
            diff = "No differences detected"
        return ToolResult(
            content=f"Firmware delta for task {context.task_id}:\n{diff}",
            metadata={"differences": str(bool(diff))},
        )


class OrderLookupTool(Tool):
    """Looks up metadata for a sales order from an in-memory store."""

    def __init__(self, name: str, **kwargs: Any) -> None:
        super().__init__(name, **kwargs)
        raw_orders: Iterable[Dict[str, Any]] = kwargs.get("orders") or []
        self._orders = {str(entry["id"]): entry for entry in raw_orders if "id" in entry}

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        order_id = str(input_text or context.metadata.get("order_id", ""))
        match = self._orders.get(order_id)
        if not match:
            return ToolResult(content=f"Order {order_id} not found", metadata={"found": "false"})
        return ToolResult(content=json.dumps(match, indent=2), metadata={"found": "true"})


class AnomalyScoringTool(Tool):
    """Assigns a lightweight anomaly score based on provided signals."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text)
        signals = payload.get("signals") if isinstance(payload, dict) else []
        score = min(100, 20 + len(signals) * 15)
        explanation = ", ".join(signals) if signals else "no detections"
        content = f"Risk score {score}/100 derived from {explanation}."
        return ToolResult(content=content, metadata={"score": str(score)})


class EdgeDeploymentPlannerTool(Tool):
    """Creates a deployment checklist for on-board/edge inference."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text)
        model = payload.get("model", "edge-model") if isinstance(payload, dict) else "edge-model"
        steps: List[str] = [
            f"Validate quantization for {model}",
            "Run hardware-in-loop smoke tests",
            "Package container image with telemetry hooks",
            "Schedule phased rollout with canary monitoring",
        ]
        plan = "\n".join(f"- {step}" for step in steps)
        return ToolResult(content=f"Deployment plan for {model}:\n{plan}")


MAGIC_BYTE_CATALOG: Sequence[Dict[str, Any]] = [
    {
        "name": "ELF (32/64-bit)",
        "magic": ["7F454C46"],
        "offset": "0x0",
        "notes": "Executables for many MCU families (common in bare-metal ELF dumps).",
    },
    {
        "name": "PE / DOS MZ",
        "magic": ["4D5A"],
        "offset": "0x0",
        "notes": "Windows PE binaries, occasionally present in vendor tooling.",
    },
    {
        "name": "Mach-O",
        "magic": ["CAFEBABE", "CFFAEDFE"],
        "offset": "0x0",
        "notes": "Apple macOS/iOS binaries.",
    },
    {
        "name": "U-Boot image",
        "magic": ["27051956"],
        "offset": "0x0",
        "notes": "Legacy U-Boot image header.",
    },
    {
        "name": "FIT image",
        "magic": ["00000000"],
        "offset": "0x0",
        "notes": "Flattened image tree; inspect device tree strings.",
    },
    {
        "name": "Android boot img",
        "magic": ["414E44524F4944"],
        "offset": "0x0",
        "notes": "Android bootloader header.",
    },
    {
        "name": "TRX (Broadcom)",
        "magic": ["2E524446", "2E534946"],
        "offset": "0x0",
        "notes": "Broadcom router container.",
    },
    {
        "name": "ZIP/APK",
        "magic": ["504B0304"],
        "offset": "0x0",
        "notes": "ZIP container / APK.",
    },
    {
        "name": "GZIP",
        "magic": ["1F8B08"],
        "offset": "0x0",
        "notes": "Compressed data stream.",
    },
    {
        "name": "BZIP2",
        "magic": ["425A68"],
        "offset": "0x0",
        "notes": "BZ2 compressed data.",
    },
    {
        "name": "XZ / LZMA2",
        "magic": ["FD377A585A00"],
        "offset": "0x0",
        "notes": "XZ compressed stream.",
    },
    {
        "name": "LZ4 frame",
        "magic": ["04224D18"],
        "offset": "0x0",
        "notes": "LZ4 frame/block.",
    },
    {
        "name": "LZMA standalone",
        "magic": ["5D00008000"],
        "offset": "0x0",
        "notes": "LZMA payload.",
    },
    {
        "name": "SquashFS",
        "magic": ["68737173"],
        "offset": "0x0",
        "notes": "SquashFS filesystem image.",
    },
    {
        "name": "CRAMFS",
        "magic": ["453DCD28"],
        "offset": "0x0",
        "notes": "CRAMFS filesystem image.",
    },
    {
        "name": "JFFS2",
        "magic": ["85190D9198"],
        "offset": "blocks",
        "notes": "JFFS2 filesystem structures (look for node markers).",
    },
    {
        "name": "UBIFS",
        "magic": ["1F8B?"],
        "offset": "-",
        "notes": "UBIFS sits on UBI volumes; inspect UBI headers.",
    },
    {
        "name": "YAFFS2",
        "magic": ["595AFF46"],
        "offset": "-",
        "notes": "YAFFS markers for NAND images.",
    },
    {
        "name": "MBR",
        "magic": ["55AA"],
        "offset": "0x1FE",
        "notes": "Partition table signature.",
    },
    {
        "name": "GPT header",
        "magic": ["454649205049"],
        "offset": "0x200",
        "notes": "GUID partition table.",
    },
    {
        "name": "Intel HEX (ASCII)",
        "magic": ["3A"],
        "offset": "0x0",
        "notes": "ASCII HEX for MCUs / bootloaders.",
    },
    {
        "name": "ARM Cortex-M vector table",
        "magic": ["20xxxxxx"],
        "offset": "0x0",
        "notes": "Vector table containing initial stack pointer + reset handler.",
    },
]


def _normalize_hex(value: str) -> str:
    cleaned = value.upper().replace("0X", "").replace(" ", "")
    return cleaned


class FirmwareIntakeTool(Tool):
    """Guides decompression + OS branching at the start of the workflow."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text)
        compressed = bool(payload.get("compressed"))
        compression = payload.get("compression_type", "unknown")
        linux_based = bool(payload.get("linux_based"))
        steps = []
        if compressed:
            steps.append(f"Uncompress firmware using {compression} utilities.")
        else:
            steps.append("Firmware already uncompressed; skip extraction.")
        if linux_based:
            steps.append("Detected Linux-based firmware → run Linux heuristics and package managers.")
        else:
            steps.append("Non-Linux firmware → follow bare-metal/RTOS paths.")
        summary = " | ".join(steps)
        metadata = {"compressed": str(compressed), "linux_based": str(linux_based)}
        return ToolResult(content=summary, metadata=metadata)


class FirmwareFormatIdentifierTool(Tool):
    """Matches provided magic bytes against the cheat sheet."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text)
        magic = _normalize_hex(str(payload.get("magic", "")))
        matches: List[str] = []
        for item in MAGIC_BYTE_CATALOG:
            for pattern in item["magic"]:
                normalized = _normalize_hex(pattern)
                if magic.startswith(normalized.rstrip("?")):
                    matches.append(f"{item['name']} (offset {item['offset']}): {item['notes']}")
                    break
        if not matches:
            matches.append("No direct match; inspect headers and consider scanning for filesystem markers.")
        return ToolResult(content="\n".join(matches), metadata={"magic": magic})


class ArchitectureInferenceTool(Tool):
    """Produces hypotheses on CPU architecture and toolchain requirements."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text)
        hint = (payload.get("arch_hint") or "").lower()
        endianness = payload.get("endianness", "little")
        findings = []
        if "arm" in hint:
            findings.append("Likely ARM Cortex; check vector table for RAM base 0x2000_0000.")
        if "mips" in hint:
            findings.append("MIPS detected; use binutils-mips and watch for big endian offsets.")
        if "xtensa" in hint or "esp" in hint:
            findings.append("Xtensa/ESP -> apply ESP32 flash map heuristics.")
        if not findings:
            findings.append("Architecture unclear; rely on strings and toolchain residue.")
        findings.append(f"Endianness appears {endianness}.")
        return ToolResult(content="\n".join(findings))


class FirmwareSectionExtractorTool(Tool):
    """Summarizes carved sections for downstream analysis."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text)
        sections = payload.get("sections") or []
        summary = "\n".join(f"- {section}" for section in sections) or "No sections listed"
        next_steps = "Load sections into Ghidra/IDA and map memory addresses."
        return ToolResult(content=f"Sections carved for {context.task_id}:\n{summary}\n{next_steps}")


class FirmwareStaticAnalyzerTool(Tool):
    """Mirrors manual static analysis steps."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text)
        components = payload.get("components") or []
        risky = [c for c in components if any(keyword in c.lower() for keyword in ["ssh", "telnet", "boot", "crypto"])]
        lines = [f"Component map ({len(components)} entries):"] + [f"- {c}" for c in components]
        if risky:
            lines.append("Priority follow-ups:")
            for comp in risky:
                lines.append(f"* {comp}")
        return ToolResult(content="\n".join(lines))


class SecretScannerTool(Tool):
    """Extracts potential secrets or credentials."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text)
        blob = payload.get("blob", "")
        indicators = []
        for token in ["password", "passwd", "api_key", "secret", "token"]:
            if token in blob.lower():
                indicators.append(token)
        content = "No secrets detected." if not indicators else f"Indicators: {', '.join(sorted(set(indicators)))}"
        return ToolResult(content=content, metadata={"hits": str(len(indicators))})


class WeaknessProfilerTool(Tool):
    """Scores weaknesses across protocols, RTOS, crypto, updater."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text)
        protocols = payload.get("protocols") or []
        rtos = payload.get("rtos", "unknown")
        crypto = payload.get("crypto", "unknown")
        updater = payload.get("updater", "unknown")
        score = 20
        if any(p.lower() in {"telnet", "ftp", "http"} for p in protocols):
            score += 20
        if isinstance(crypto, str) and "md5" in crypto.lower():
            score += 15
        if updater and "unsigned" in str(updater).lower():
            score += 25
        details = [
            f"Protocols: {protocols}",
            f"RTOS: {rtos}",
            f"Crypto: {crypto}",
            f"Updater: {updater}",
            f"Risk score: {min(score, 100)}",
        ]
        return ToolResult(content="\n".join(details), metadata={"score": str(min(score, 100))})


class VerificationPlannerTool(Tool):
    """Creates a test/verify checklist from earlier findings."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text)
        findings = payload.get("findings") or []
        steps = [f"Reproduce finding: {finding}" for finding in findings] or ["No outstanding findings to verify."]
        steps.append("Document results and update Azure DevOps/Dradis.")
        return ToolResult(content="\n".join(steps))


def register_builtin_tools(registry: ToolRegistry) -> None:
    """Register built-in tool factories."""

    registry.register_factory("nmap_scan", lambda: NmapScanTool(name="nmap_scan"), overwrite=True)
    registry.register_factory("firmware_diff", lambda: FirmwareDiffTool(name="firmware_diff"), overwrite=True)
    registry.register_factory("order_lookup", lambda: OrderLookupTool(name="order_lookup"), overwrite=True)
    registry.register_factory(
        "anomaly_scoring", lambda: AnomalyScoringTool(name="anomaly_scoring"), overwrite=True
    )
    registry.register_factory(
        "edge_deployment_planner",
        lambda: EdgeDeploymentPlannerTool(name="edge_deployment_planner"),
        overwrite=True,
    )
    registry.register_factory("firmware_intake", lambda: FirmwareIntakeTool(name="firmware_intake"), overwrite=True)
    registry.register_factory(
        "firmware_format_identifier",
        lambda: FirmwareFormatIdentifierTool(name="firmware_format_identifier"),
        overwrite=True,
    )
    registry.register_factory(
        "architecture_inference", lambda: ArchitectureInferenceTool(name="architecture_inference"), overwrite=True
    )
    registry.register_factory(
        "firmware_section_extractor",
        lambda: FirmwareSectionExtractorTool(name="firmware_section_extractor"),
        overwrite=True,
    )
    registry.register_factory(
        "firmware_static_analyzer",
        lambda: FirmwareStaticAnalyzerTool(name="firmware_static_analyzer"),
        overwrite=True,
    )
    registry.register_factory("secret_scanner", lambda: SecretScannerTool(name="secret_scanner"), overwrite=True)
    registry.register_factory(
        "weakness_profiler", lambda: WeaknessProfilerTool(name="weakness_profiler"), overwrite=True
    )
    registry.register_factory(
        "verification_planner", lambda: VerificationPlannerTool(name="verification_planner"), overwrite=True
    )
