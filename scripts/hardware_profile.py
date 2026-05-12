"""Hardware detection for model recommendation.

This module only reads OS metadata. It does not contact Ollama and never starts
or loads a model.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.os_profile import detect_os


@dataclass
class GPUInfo:
    name: str
    vendor: str = ""
    memory_gb: float | None = None


@dataclass
class HardwareProfile:
    os: str
    arch: str
    cpu_brand: str
    physical_cores: int
    logical_cores: int
    ram_gb: float
    gpus: list[GPUInfo] = field(default_factory=list)
    os_family: str = ""
    supported_os: bool = False

    @property
    def apple_silicon(self) -> bool:
        return self.os_family == "macos" and self.arch in {"arm64", "aarch64"}

    @property
    def windows_pc(self) -> bool:
        return self.os_family == "windows"

    @property
    def has_gpu(self) -> bool:
        return bool(self.gpus) or self.apple_silicon

    @property
    def primary_gpu(self) -> GPUInfo | None:
        if not self.gpus:
            return None
        return max(self.gpus, key=lambda gpu: gpu.memory_gb or 0)

    @property
    def dedicated_vram_gb(self) -> float | None:
        values = [gpu.memory_gb for gpu in self.gpus if gpu.memory_gb is not None]
        return max(values) if values else None

    @property
    def nvidia_gpu(self) -> bool:
        return any(gpu.vendor.lower() == "nvidia" or "nvidia" in gpu.name.lower() for gpu in self.gpus)


def _run_text(cmd: list[str], timeout: float = 3.0) -> str:
    try:
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=timeout).decode().strip()
    except Exception:
        return ""


def _sysctl_int(name: str, default: int = 0) -> int:
    raw = _run_text(["sysctl", "-n", name])
    try:
        return int(raw)
    except ValueError:
        return default


def _darwin_gpus() -> list[GPUInfo]:
    raw = _run_text(["system_profiler", "SPDisplaysDataType", "-json"], timeout=5.0)
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []

    results: list[GPUInfo] = []
    for item in payload.get("SPDisplaysDataType", []) or []:
        name = str(item.get("sppci_model") or item.get("_name") or "Apple GPU")
        vendor = str(item.get("spdisplays_vendor") or "")
        memory: float | None = None
        vram = str(item.get("spdisplays_vram") or item.get("spdisplays_vram_shared") or "")
        digits = "".join(ch if ch.isdigit() or ch == "." else " " for ch in vram).split()
        if digits:
            try:
                memory = float(digits[0])
                if "mb" in vram.lower():
                    memory = memory / 1024
            except ValueError:
                memory = None
        results.append(GPUInfo(name=name, vendor=vendor, memory_gb=memory))
    return results


def _linux_ram_gb() -> float:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return 8.0
    for line in meminfo.read_text(errors="replace").splitlines():
        if line.startswith("MemTotal:"):
            try:
                return int(line.split()[1]) / (1024**2)
            except Exception:
                return 8.0
    return 8.0


def _linux_cpu_brand() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(errors="replace").splitlines():
            if "model name" in line:
                return line.split(":", 1)[1].strip()
    return platform.processor() or "Unknown CPU"


def _linux_gpus() -> list[GPUInfo]:
    raw = _run_text(["lspci"], timeout=3.0)
    if not raw:
        return []
    results: list[GPUInfo] = []
    for line in raw.splitlines():
        lower = line.lower()
        if "vga compatible controller" in lower or "3d controller" in lower:
            name = line.split(":", 2)[-1].strip()
            vendor = "nvidia" if "nvidia" in lower else "amd" if "amd" in lower else "intel" if "intel" in lower else ""
            results.append(GPUInfo(name=name, vendor=vendor))
    return results


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _windows_ram_gb() -> float:
    try:
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return float(status.ullTotalPhys) / (1024**3)
    except Exception:
        pass
    return 8.0


def _powershell_json(command: str, timeout: float = 4.0) -> Any:
    raw = _run_text(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        timeout=timeout,
    )
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()


def _windows_cpu_brand() -> str:
    raw = _powershell_json(
        "Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name | ConvertTo-Json -Compress",
        timeout=3.0,
    )
    if isinstance(raw, str) and raw:
        return raw
    return os.getenv("PROCESSOR_IDENTIFIER") or platform.processor() or "Unknown CPU"


def _windows_cpu_counts() -> tuple[int, int]:
    raw = _powershell_json(
        "Get-CimInstance Win32_Processor | Select-Object -First 1 NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json -Compress",
        timeout=3.0,
    )
    logical_default = os.cpu_count() or 4
    if isinstance(raw, dict):
        try:
            physical = int(raw.get("NumberOfCores") or logical_default)
            logical = int(raw.get("NumberOfLogicalProcessors") or logical_default)
            return max(1, physical), max(1, logical)
        except (TypeError, ValueError):
            pass
    return logical_default, logical_default


def _nvidia_smi_gpus() -> list[GPUInfo]:
    raw = _run_text(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits",
        ],
        timeout=3.0,
    )
    if not raw:
        return []
    results: list[GPUInfo] = []
    for line in raw.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if not parts or not parts[0]:
            continue
        memory: float | None = None
        if len(parts) > 1:
            try:
                memory = round(float(parts[1]) / 1024, 2)
            except ValueError:
                memory = None
        results.append(GPUInfo(name=parts[0], vendor="nvidia", memory_gb=memory))
    return results


def _windows_gpus() -> list[GPUInfo]:
    smi_gpus = _nvidia_smi_gpus()
    raw = _powershell_json(
        "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion | ConvertTo-Json -Compress",
        timeout=4.0,
    )
    if raw is None:
        return smi_gpus
    items = raw if isinstance(raw, list) else [raw]
    results: list[GPUInfo] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "Windows GPU")
        lower = name.lower()
        vendor = "nvidia" if "nvidia" in lower else "amd" if "amd" in lower else "intel" if "intel" in lower else ""
        memory: float | None = None
        try:
            raw_memory = item.get("AdapterRAM")
            if raw_memory:
                memory = round(float(raw_memory) / (1024**3), 2)
        except (TypeError, ValueError):
            memory = None
        results.append(GPUInfo(name=name, vendor=vendor, memory_gb=memory))

    # Win32_VideoController.AdapterRAM is often capped or stale on modern NVIDIA
    # cards. Prefer nvidia-smi VRAM numbers when they are available.
    merged: list[GPUInfo] = []
    used_smi: set[int] = set()
    for gpu in results:
        if gpu.vendor == "nvidia":
            match_index = next(
                (
                    idx
                    for idx, smi_gpu in enumerate(smi_gpus)
                    if idx not in used_smi
                    and (
                        smi_gpu.name.lower() in gpu.name.lower()
                        or gpu.name.lower() in smi_gpu.name.lower()
                        or "nvidia" in gpu.name.lower()
                    )
                ),
                None,
            )
            if match_index is not None:
                used_smi.add(match_index)
                merged.append(smi_gpus[match_index])
                continue
        merged.append(gpu)
    for idx, smi_gpu in enumerate(smi_gpus):
        if idx not in used_smi:
            merged.append(smi_gpu)
    return merged


def detect_hardware() -> HardwareProfile:
    os_profile = detect_os()
    system = platform.system() or "Unknown"
    arch = platform.machine() or "unknown"

    if system == "Darwin":
        ram_gb = _sysctl_int("hw.memsize", 8 * 1024**3) / (1024**3)
        physical = _sysctl_int("hw.physicalcpu", os.cpu_count() or 4)
        logical = _sysctl_int("hw.logicalcpu", os.cpu_count() or 4)
        cpu_brand = _run_text(["sysctl", "-n", "machdep.cpu.brand_string"]) or "Apple Silicon"
        return HardwareProfile(
            os=system,
            arch=arch,
            cpu_brand=cpu_brand,
            physical_cores=physical,
            logical_cores=logical,
            ram_gb=ram_gb,
            gpus=_darwin_gpus(),
            os_family=os_profile.family,
            supported_os=os_profile.supported,
        )

    if system == "Windows":
        physical, logical = _windows_cpu_counts()
        return HardwareProfile(
            os=system,
            arch=arch,
            cpu_brand=_windows_cpu_brand(),
            physical_cores=physical,
            logical_cores=logical,
            ram_gb=_windows_ram_gb(),
            gpus=_windows_gpus(),
            os_family=os_profile.family,
            supported_os=os_profile.supported,
        )

    if system == "Linux":
        logical = os.cpu_count() or 4
        return HardwareProfile(
            os=system,
            arch=arch,
            cpu_brand=_linux_cpu_brand(),
            physical_cores=logical,
            logical_cores=logical,
            ram_gb=_linux_ram_gb(),
            gpus=_linux_gpus(),
            os_family=os_profile.family,
            supported_os=os_profile.supported,
        )

    return HardwareProfile(
        os=system,
        arch=arch,
        cpu_brand=platform.processor() or "Unknown CPU",
        physical_cores=os.cpu_count() or 4,
        logical_cores=os.cpu_count() or 4,
        ram_gb=8.0,
        gpus=[],
        os_family=os_profile.family,
        supported_os=os_profile.supported,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Print local hardware profile as JSON")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args()
    profile = detect_hardware()
    print(json.dumps(asdict(profile), indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
