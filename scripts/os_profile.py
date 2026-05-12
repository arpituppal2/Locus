"""Operating-system profile and optimization hints for Locus.

The app currently supports macOS and Windows. This module is passive: it only
reads local OS metadata and never starts browsers, models, or external services.
"""
from __future__ import annotations

import json
import os
import platform
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SUPPORTED_OS = {"macos", "windows"}


@dataclass(frozen=True)
class OSProfile:
    family: str
    name: str
    version: str
    release: str
    arch: str
    supported: bool
    state_dir: str
    setup_copy: str
    permission_copy: str
    optimization_notes: list[str]
    checklist_notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _state_dir(family: str) -> Path:
    if family == "windows":
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Locus"
    return Path.home() / ".local-computer"


def detect_os() -> OSProfile:
    system = platform.system()
    arch = platform.machine() or "unknown"
    version = platform.version() or ""
    release = platform.release() or ""

    if system == "Darwin":
        return OSProfile(
            family="macos",
            name="macOS",
            version=platform.mac_ver()[0] or version,
            release=release,
            arch=arch,
            supported=True,
            state_dir=str(_state_dir("macos")),
            setup_copy="macOS detected. Locus will optimize for unified memory, Metal/MPS limits, and local app permissions.",
            permission_copy="Approve Full Disk Access for protected folders and Accessibility for global shortcuts/app control.",
            optimization_notes=[
                "Uses conservative unified-memory budgets.",
                "Caps MPS/GPU pressure at the configured GPU limit.",
                "Keeps one loaded local model and one local job by default on low-RAM Macs.",
            ],
            checklist_notes=[
                "Full Disk Access is optional but recommended for Mail, Messages, Safari, and broad folder access.",
                "Accessibility is optional but recommended for global shortcuts and app-control automation.",
            ],
        )

    if system == "Windows":
        return OSProfile(
            family="windows",
            name="Windows",
            version=version,
            release=release,
            arch=arch,
            supported=True,
            state_dir=str(_state_dir("windows")),
            setup_copy="Windows detected. Locus will optimize for system memory, local browser automation, and Windows app data folders.",
            permission_copy="Run from a folder you own. For protected folders, approve Windows security prompts when they appear.",
            optimization_notes=[
                "Uses conservative system-memory budgets and avoids macOS-only MPS settings.",
                "Keeps one loaded local model and one local job by default on low-RAM PCs.",
                "Stores local app state under the Windows local app data folder.",
            ],
            checklist_notes=[
                "Windows does not need macOS Full Disk Access.",
                "Browser automation uses the local Playwright Chromium install.",
                "Protected folders may still require Windows security approval.",
            ],
        )

    display = system or "Unsupported OS"
    return OSProfile(
        family=system.lower() or "unknown",
        name=display,
        version=version,
        release=release,
        arch=arch,
        supported=False,
        state_dir=str(_state_dir("unsupported")),
        setup_copy=f"{display} detected. Locus currently supports macOS and Windows only.",
        permission_copy="Use macOS or Windows for the supported app experience.",
        optimization_notes=["Unsupported OS: local app optimizations are not available."],
        checklist_notes=["Install and run Locus on macOS or Windows."],
    )


def is_supported_os() -> bool:
    return detect_os().supported


def main() -> None:
    print(json.dumps(detect_os().to_dict(), indent=2))


if __name__ == "__main__":
    main()
