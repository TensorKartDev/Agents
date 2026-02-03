"""Built-in tools that demonstrate different domains."""

from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import yaml

from .base import Tool, ToolContext, ToolResult
from .registry import ToolRegistry


def _load_structured(text: Any) -> Any:
    # If the caller already passed a parsed structure (dict/list), return it as-is.
    if isinstance(text, (dict, list)):
        return text
    # Only attempt to parse strings; non-string, non-structured inputs are returned.
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError:
            return text


def _command_available(binary: str) -> bool:
    return shutil.which(binary) is not None


def _run_command(args: Sequence[str], *, timeout: int = 120, cwd: Path | None = None) -> Dict[str, Any]:
    try:
        proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return {"code": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}
    except FileNotFoundError:
        return {"code": 127, "stdout": "", "stderr": f"{args[0]} not found"}
    except subprocess.TimeoutExpired:
        return {"code": -1, "stdout": "", "stderr": f"timeout after {timeout}s"}
    except Exception as exc:  # pragma: no cover - defensive guard
        return {"code": 1, "stdout": "", "stderr": f"failed to run {' '.join(args)}: {exc}"}


def _summarize(label: str, result: Dict[str, Any], *, limit: int = 1200) -> str:
    output = (result.get("stdout") or result.get("stderr") or "").strip()
    if not output:
        output = "<no output>"
    if len(output) > limit:
        output = output[:limit] + "\n...[truncated]..."
    status = "ok" if result.get("code") == 0 else f"exit {result.get('code')}"
    return f"{label} ({status}):\n{output}"


def _resolve_path(payload: Any) -> Path | None:
    if not isinstance(payload, dict):
        return None
    candidate = payload.get("path") or payload.get("firmware_path") or payload.get("file")
    if not candidate:
        return None
    return Path(str(candidate))


def _validate_path(path: Path) -> str | None:
    if not path.exists():
        return f"File {path} not found"
    if not path.is_file():
        return f"{path} is not a regular file"
    return None


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
        payload = _load_structured(input_text) or {}
        if not isinstance(payload, dict):
            payload = {}
        firmware_path = _resolve_path(payload)
        if not firmware_path:
            return ToolResult(
                content="Provide firmware 'path' to operate on (example: /tmp/fw.bin).",
                metadata={"error": "missing_path"},
            )
        validation_error = _validate_path(firmware_path)
        if validation_error:
            return ToolResult(content=validation_error, metadata={"error": "missing_file"})

        results: List[str] = []
        file_result = _run_command(["file", "-b", str(firmware_path)])
        results.append(_summarize("file", file_result))

        extract = bool(payload.get("extract") or self.config.get("extract"))
        output_dir = Path(payload.get("output_dir") or firmware_path.parent / f"{firmware_path.stem}_extract")
        if extract:
            if _command_available("binwalk"):
                output_dir.mkdir(parents=True, exist_ok=True)
                timeout = int(payload.get("timeout", 300))
                args = ["binwalk", "--extract", "--directory", str(output_dir), str(firmware_path)]
                results.append(_summarize("binwalk --extract", _run_command(args, timeout=timeout)))
            else:
                results.append("binwalk not available on PATH; skipping extraction.")
        else:
            results.append("Extraction disabled (set extract: true to carve sections with binwalk).")

        metadata = {"path": str(firmware_path)}
        if extract:
            metadata["output_dir"] = str(output_dir)
        return ToolResult(content="\n\n".join(results), metadata=metadata)


class FirmwareFormatIdentifierTool(Tool):
    """Matches headers/magic bytes using real file/binwalk output."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text) or {}
        if not isinstance(payload, dict):
            payload = {}
        firmware_path = _resolve_path(payload)
        if not firmware_path:
            return ToolResult(
                content="Provide firmware 'path' so the tool can read headers (example: /tmp/fw.bin).",
                metadata={"error": "missing_path"},
            )
        validation_error = _validate_path(firmware_path)
        if validation_error:
            return ToolResult(content=validation_error, metadata={"error": "missing_file"})

        sections: List[str] = []
        sections.append(_summarize("file", _run_command(["file", str(firmware_path)])))

        if _command_available("xxd"):
            sections.append(_summarize("xxd -l 64", _run_command(["xxd", "-l", "64", str(firmware_path)])))
        elif _command_available("hexdump"):
            sections.append(_summarize("hexdump -C -n 64", _run_command(["hexdump", "-C", "-n", "64", str(firmware_path)])))
        else:
            sections.append("xxd/hexdump unavailable; skipping inline header bytes.")

        if _command_available("binwalk"):
            sections.append(
                _summarize("binwalk --signature", _run_command(["binwalk", "--signature", "--nobanner", str(firmware_path)]))
            )
        else:
            sections.append("binwalk not available on PATH; install it to see signature matches.")

        return ToolResult(content="\n\n".join(sections), metadata={"path": str(firmware_path)})


class ArchitectureInferenceTool(Tool):
    """Derives CPU/endianness using binutils (`readelf`/`objdump`)."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text) or {}
        if not isinstance(payload, dict):
            payload = {}
        firmware_path = _resolve_path(payload)
        if not firmware_path:
            return ToolResult(
                content="Provide firmware 'path' so readelf/objdump can inspect it.",
                metadata={"error": "missing_path"},
            )
        validation_error = _validate_path(firmware_path)
        if validation_error:
            return ToolResult(content=validation_error, metadata={"error": "missing_file"})

        reports: List[str] = []
        machine: str | None = None
        endian: str | None = None

        if _command_available("readelf"):
            result = _run_command(["readelf", "-h", str(firmware_path)])
            reports.append(_summarize("readelf -h", result))
            header_text = result.get("stdout", "")
            machine_match = re.search(r"Machine:\s*(.+)", header_text)
            data_match = re.search(r"Data:\s*(.+)", header_text)
            if machine_match:
                machine = machine_match.group(1).strip()
            if data_match:
                endian = data_match.group(1).strip()
        elif _command_available("objdump"):
            result = _run_command(["objdump", "-f", str(firmware_path)])
            reports.append(_summarize("objdump -f", result))
            header_text = result.get("stdout", "")
            machine_match = re.search(r"architecture:\s*(\S+)", header_text)
            if machine_match:
                machine = machine_match.group(1).strip()
        else:
            reports.append("Install binutils (readelf/objdump) to infer architecture directly.")

        if _command_available("file"):
            reports.append(_summarize("file", _run_command(["file", str(firmware_path)])))

        metadata = {"path": str(firmware_path)}
        if machine:
            metadata["machine"] = machine
        if endian:
            metadata["endianness"] = endian
        if not machine and not endian:
            reports.append("Could not parse architecture fields; inspect the outputs above.")
        return ToolResult(content="\n\n".join(reports), metadata=metadata)


class FirmwareSectionExtractorTool(Tool):
    """Uses binwalk to enumerate (and optionally carve) sections."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text) or {}
        if not isinstance(payload, dict):
            payload = {}
        firmware_path = _resolve_path(payload)
        if not firmware_path:
            return ToolResult(
                content="Provide firmware 'path' so binwalk can enumerate sections.",
                metadata={"error": "missing_path"},
            )
        validation_error = _validate_path(firmware_path)
        if validation_error:
            return ToolResult(content=validation_error, metadata={"error": "missing_file"})
        if not _command_available("binwalk"):
            return ToolResult(content="binwalk not available on PATH; install it to scan the firmware.", metadata={"error": "missing_binwalk"})

        reports: List[str] = []
        reports.append(_summarize("binwalk --nobanner", _run_command(["binwalk", "--nobanner", str(firmware_path)])))

        extract = bool(payload.get("extract") or self.config.get("extract"))
        if extract:
            output_dir = Path(payload.get("output_dir") or firmware_path.parent / f"{firmware_path.stem}_sections")
            output_dir.mkdir(parents=True, exist_ok=True)
            timeout = int(payload.get("timeout", 300))
            reports.append(
                _summarize(
                    "binwalk --dd=.*",
                    _run_command(["binwalk", "--nobanner", "--dd=.*", "--directory", str(output_dir), str(firmware_path)], timeout=timeout),
                )
            )
            metadata = {"path": str(firmware_path), "output_dir": str(output_dir)}
        else:
            reports.append("Extraction disabled (set extract: true to carve sections with --dd=.*).")
            metadata = {"path": str(firmware_path)}

        return ToolResult(content="\n\n".join(reports), metadata=metadata)


class FirmwareStaticAnalyzerTool(Tool):
    """Runs strings/grep against the firmware to surface interesting components."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text) or {}
        if not isinstance(payload, dict):
            payload = {}
        firmware_path = _resolve_path(payload)
        if not firmware_path:
            return ToolResult(
                content="Provide firmware 'path' for static analysis.",
                metadata={"error": "missing_path"},
            )
        validation_error = _validate_path(firmware_path)
        if validation_error:
            return ToolResult(content=validation_error, metadata={"error": "missing_file"})

        patterns = payload.get("patterns") or self.config.get("patterns") or [
            "ssh",
            "telnet",
            "dropbear",
            "httpd",
            "ftp",
            "ota",
            "upgrade",
            "busybox",
            "openssl",
            "shadow",
            "passwd",
        ]
        reports: List[str] = []
        timeout = int(payload.get("timeout", 180))

        if _command_available("rg"):
            regex = "|".join(patterns)
            reports.append(
                _summarize(
                    "ripgrep service scan",
                    _run_command(["rg", "-n", "-i", "--no-config", "-e", regex, str(firmware_path)], timeout=timeout),
                )
            )
        elif _command_available("strings"):
            strings_result = _run_command(["strings", "-n", "6", str(firmware_path)], timeout=timeout)
            lines = strings_result.get("stdout", "").splitlines()
            matches: List[str] = []
            for line in lines:
                lowered = line.lower()
                if any(p.lower() in lowered for p in patterns):
                    matches.append(line)
                if len(matches) >= 40:
                    break
            reports.append(_summarize("strings", strings_result))
            reports.append("Keyword hits:\n" + ("\n".join(matches) if matches else "No keyword hits found."))
        else:
            return ToolResult(
                content="Neither ripgrep nor strings is available; install one to perform static scanning.",
                metadata={"error": "missing_scanner"},
            )

        return ToolResult(content="\n\n".join(reports), metadata={"path": str(firmware_path)})


class SecretScannerTool(Tool):
    """Extracts potential secrets or credentials."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text) or {}
        if not isinstance(payload, dict):
            payload = {}
        firmware_path = _resolve_path(payload)
        pattern = payload.get("pattern") or r"(?i)(password|passwd|api[_-]?key|secret|token|authorization|bearer|private key|ssh-rsa)"
        timeout = int(payload.get("timeout", 180))

        if firmware_path:
            validation_error = _validate_path(firmware_path)
            if validation_error:
                return ToolResult(content=validation_error, metadata={"error": "missing_file"})
            if _command_available("rg"):
                result = _run_command(["rg", "-n", "--no-config", "-e", pattern, str(firmware_path)], timeout=timeout)
                summary = _summarize("ripgrep secret scan", result)
                hits = len(result.get("stdout", "").splitlines())
                return ToolResult(content=summary, metadata={"path": str(firmware_path), "hits": str(hits)})
            try:
                text = firmware_path.read_bytes().decode(errors="ignore")
            except Exception as exc:  # pragma: no cover - guard
                return ToolResult(content=f"Failed to read {firmware_path}: {exc}", metadata={"error": "read_failed"})
        else:
            text = str(payload.get("blob", ""))
            if not text and isinstance(input_text, str):
                text = input_text

        matches = re.findall(pattern, text)
        unique_hits = sorted(set(match.lower() for match in matches))
        content = "No secrets detected." if not unique_hits else f"Indicators: {', '.join(unique_hits)}"
        return ToolResult(content=content, metadata={"hits": str(len(matches))})


class WeaknessProfilerTool(Tool):
    """Scores weaknesses across protocols, RTOS, crypto, updater using real scans."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text) or {}
        if not isinstance(payload, dict):
            payload = {}
        firmware_path = _resolve_path(payload)
        timeout = int(payload.get("timeout", 180))

        keyword_map = {
            "telnet": r"telnetd",
            "ftp": r"ftpd|vsftpd",
            "http": r"httpd|lighttpd|nginx|apache",
            "ssh": r"sshd|dropbear",
            "ota": r"ota|upgrade|fw_update",
            "md5": r"md5",
            "unsigned": r"unsigned|no\s*signature",
        }
        detected: Dict[str, List[str]] = {}

        if firmware_path:
            validation_error = _validate_path(firmware_path)
            if validation_error:
                return ToolResult(content=validation_error, metadata={"error": "missing_file"})
            if _command_available("rg"):
                for label, regex in keyword_map.items():
                    result = _run_command(
                        ["rg", "-n", "-i", "--no-config", "-m", "5", "-e", regex, str(firmware_path)], timeout=timeout
                    )
                    lines = [line for line in result.get("stdout", "").splitlines() if line.strip()]
                    if lines:
                        detected[label] = lines[:5]
            elif _command_available("strings"):
                strings_result = _run_command(["strings", "-n", "6", str(firmware_path)], timeout=timeout)
                lines = strings_result.get("stdout", "").splitlines()
                for label, regex in keyword_map.items():
                    compiled = re.compile(regex, re.IGNORECASE)
                    matches = [line for line in lines if compiled.search(line)]
                    if matches:
                        detected[label] = matches[:5]
            else:
                return ToolResult(
                    content="Install ripgrep or strings to profile weaknesses from the firmware image.",
                    metadata={"error": "missing_scanner"},
                )

        protocols = list(detected.keys()) or payload.get("protocols") or []
        rtos = payload.get("rtos", "unknown")
        crypto = payload.get("crypto", "unknown")
        updater = payload.get("updater", "unknown")

        score = 20
        if any(p.lower() in {"telnet", "ftp", "http"} for p in protocols):
            score += 20
        if detected.get("md5") or (isinstance(crypto, str) and "md5" in crypto.lower()):
            score += 15
        if detected.get("unsigned") or (updater and "unsigned" in str(updater).lower()):
            score += 25
        if detected.get("ota"):
            score += 5

        details = [f"Protocols detected: {protocols or 'none'}", f"RTOS: {rtos}", f"Crypto: {crypto}", f"Updater: {updater}"]
        if detected:
            details.append("Evidence excerpts:")
            for label, lines in detected.items():
                for line in lines:
                    details.append(f"[{label}] {line}")
        details.append(f"Risk score: {min(score, 100)}")
        metadata = {"score": str(min(score, 100))}
        if firmware_path:
            metadata["path"] = str(firmware_path)
        return ToolResult(content="\n".join(details), metadata=metadata)


class DiskUsageTriageTool(Tool):
    """Uses df/du to assess disk usage and recommend cleanup targets."""

    def run(self, *, input_text: str, context: ToolContext) -> ToolResult:
        payload = _load_structured(input_text) or {}
        if not isinstance(payload, dict):
            payload = {}

        if not _command_available("df") or not _command_available("du"):
            return ToolResult(
                content="Missing required tools: ensure both df and du are available on PATH.",
                metadata={"error": "missing_tools"},
            )

        path_value = payload.get("path") or self.config.get("path") or "/"
        target = Path(str(path_value))
        if not target.exists():
            return ToolResult(content=f"Path {target} not found.", metadata={"error": "missing_path"})
        if not target.is_dir():
            return ToolResult(content=f"{target} is not a directory.", metadata={"error": "not_directory"})

        df_cmd = ["df", "-P", "-k", str(target)]
        df_result = _run_command(df_cmd)
        df_summary = _summarize("df -P -k", df_result)
        if df_result.get("code") != 0:
            return ToolResult(content=df_summary, metadata={"error": "df_failed"})

        percent_used: int | None = None
        available_kb: int | None = None
        stdout_lines = df_result.get("stdout", "").splitlines()
        if len(stdout_lines) >= 2:
            for line in stdout_lines[1:]:
                columns = [col for col in line.split() if col]
                percent_column = next((col for col in columns if col.endswith("%")), None)
                if percent_column:
                    try:
                        percent_used = int(percent_column.strip("%"))
                    except ValueError:
                        percent_used = None
                if len(columns) >= 5:
                    try:
                        available_kb = int(columns[3])
                    except ValueError:
                        available_kb = None
                if percent_used is not None:
                    break

        status = "unknown"
        if percent_used is not None:
            if percent_used >= 85:
                status = "critical"
            elif percent_used >= 70:
                status = "warning"
            else:
                status = "ok"

        output_sections: List[str] = [
            f"Executed on host: {context.metadata.get('host', 'local')}",
            f"Working path: {target}",
            f"Command: {' '.join(df_cmd)}",
            df_summary,
        ]
        metadata: Dict[str, str] = {"path": str(target), "status": status}
        if percent_used is not None:
            metadata["percent_used"] = str(percent_used)
        if available_kb is not None:
            metadata["available_kb"] = str(available_kb)

        if status in {"warning", "critical"}:
            timeout = int(payload.get("timeout", 40))
            du_cmd = ["du", "-x", "-k", "-d", "1", str(target)]
            du_result = _run_command(du_cmd, timeout=timeout)
            output_sections.append(f"Command: {' '.join(du_cmd)}")
            output_sections.append(_summarize("du -x -k -d 1", du_result))
            largest: List[tuple[int, str]] = []
            for line in du_result.get("stdout", "").splitlines():
                parts = line.split(None, 1)
                if len(parts) != 2:
                    continue
                try:
                    size_kb = int(parts[0])
                except ValueError:
                    continue
                path = parts[1]
                if path == str(target):
                    continue
                largest.append((size_kb, path))
            largest.sort(key=lambda item: item[0], reverse=True)
            if largest:
                top_n = int(payload.get("top_n", 5))
                report_lines = []
                for size_kb, path in largest[:top_n]:
                    size_mb = size_kb / 1024
                    report_lines.append(f"- {path}: {size_mb:.1f} MiB")
                output_sections.append("Top directories by size:\n" + "\n".join(report_lines))
        else:
            output_sections.append("Disk usage within acceptable thresholds; no cleanup required.")

        min_mb = int(payload.get("min_mb", 100))
        find_cmd = [
            "find",
            str(target),
            "-xdev",
            "-type",
            "f",
            "-size",
            f"+{min_mb}M",
            "-printf",
            "%s %p\n",
        ]
        find_result = _run_command(find_cmd, timeout=int(payload.get("timeout", 40)))
        output_sections.append(f"Command: {' '.join(find_cmd)}")
        output_sections.append(_summarize(f"find files > {min_mb}M", find_result))

        large_files: List[tuple[int, str]] = []
        for line in find_result.get("stdout", "").splitlines():
            parts = line.split(" ", 1)
            if len(parts) != 2:
                continue
            try:
                size_bytes = int(parts[0])
            except ValueError:
                continue
            large_files.append((size_bytes, parts[1]))
        large_files.sort(key=lambda item: item[0], reverse=True)
        if large_files:
            top_files = int(payload.get("top_files", 10))
            lines = []
            for size_bytes, path in large_files[:top_files]:
                size_mb = size_bytes / (1024 * 1024)
                lines.append(f"- {path}: {size_mb:.1f} MiB")
            output_sections.append(f"Largest files (> {min_mb} MiB):\n" + "\n".join(lines))
        else:
            output_sections.append(f"No files larger than {min_mb} MiB found under {target}.")

        return ToolResult(content="\n\n".join(output_sections), metadata=metadata)


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
        "disk_usage_triage", lambda: DiskUsageTriageTool(name="disk_usage_triage"), overwrite=True
    )
    registry.register_factory(
        "verification_planner", lambda: VerificationPlannerTool(name="verification_planner"), overwrite=True
    )
