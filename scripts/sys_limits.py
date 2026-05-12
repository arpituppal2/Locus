"""Compatibility helpers for local inference limits."""
from __future__ import annotations
import os
import platform
import subprocess

from scripts.hardware_profile import detect_hardware
from scripts.resource_policy import resource_budget


def get_total_ram_gb() -> float:
    try:
        if platform.system() == "Darwin":
            mem_bytes = int(
                subprocess.check_output(["sysctl", "-n", "hw.memsize"]).decode().strip()
            )
            return mem_bytes / (1024 ** 3)
        if platform.system() == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / (1024 ** 2)
    except Exception:
        pass
    return 8.0


def get_total_cores() -> int:
    return os.cpu_count() or 4


def planner_options() -> dict:
    profile = detect_hardware()
    budget = resource_budget(profile)
    cores = get_total_cores()
    threads = max(2, min(4 if budget.low_ram_mode else 8, cores - 2 if cores > 3 else cores))
    if budget.usable_for_models_gb <= 3.8:
        ctx = 2048
    elif budget.usable_for_models_gb <= 5.5:
        ctx = 3072
    elif budget.usable_for_models_gb <= 8.5:
        ctx = 4096
    else:
        ctx = 8192
    return {"temperature": 0.0, "num_thread": threads, "num_ctx": ctx, "num_gpu": 999 if profile.has_gpu else 0}


def observer_text_limit() -> int:
    budget = resource_budget()
    if budget.usable_for_models_gb <= 3.8:
        return 1200
    if budget.usable_for_models_gb <= 5.5:
        return 1800
    return 3500 if get_total_ram_gb() >= 14 else 2200
