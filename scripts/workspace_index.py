"""Workspace indexing and briefing.

This gives Locus a durable view of the folder it is inhabiting without
using a model: project type, manifests, scripts, tests, docs, TODOs, and git
state. The index is cached under ~/.local-computer/workspaces.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.runtime_policy import workspace_root

try:
    import tomllib
except Exception:  # pragma: no cover - Python < 3.11 fallback
    tomllib = None


STATE_DIR = Path.home() / ".local-computer" / "workspaces"
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "outputs", "logs", "uploads", "dist", "build"}
LANG_BY_SUFFIX = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".html": "HTML",
    ".css": "CSS",
    ".sh": "Shell",
    ".json": "JSON",
    ".md": "Markdown",
    ".toml": "TOML",
    ".yaml": "YAML",
    ".yml": "YAML",
}
TEXT_SUFFIXES = set(LANG_BY_SUFFIX) | {".txt", ".csv", ".tsv"}
TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b[:\s-]*(.*)")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _workspace_id(root: Path) -> str:
    digest = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    return digest


def _cache_path(root: Path) -> Path:
    return STATE_DIR / f"{_workspace_id(root)}.json"


def _is_skipped(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _run(command: list[str], cwd: Path, timeout: float = 5.0) -> dict[str, Any]:
    try:
        result = subprocess.run(command, cwd=str(cwd), text=True, capture_output=True, timeout=timeout)
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except Exception as exc:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": str(exc)}


def _files(root: Path, limit: int = 8000) -> list[Path]:
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if _is_skipped(path):
            continue
        if path.is_file():
            found.append(path)
            if len(found) >= limit:
                break
    return found


def _parse_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_toml(path: Path) -> dict[str, Any]:
    if tomllib is None:
        return {}
    try:
        data = tomllib.loads(path.read_text(errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _manifest_info(root: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"files": [], "package_scripts": {}, "dependencies": {}, "python": {}, "test_commands": []}

    package_json = root / "package.json"
    if package_json.exists():
        data = _parse_json(package_json)
        info["files"].append("package.json")
        scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
        deps = {}
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            if isinstance(data.get(key), dict):
                deps[key] = list(data[key].keys())[:60]
        info["package_scripts"] = scripts
        info["dependencies"]["node"] = deps
        for name, command in scripts.items():
            lowered = f"{name} {command}".lower()
            if any(word in lowered for word in ["test", "lint", "typecheck", "check", "build"]):
                info["test_commands"].append(f"npm run {name}")

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        data = _parse_toml(pyproject)
        info["files"].append("pyproject.toml")
        project = data.get("project") if isinstance(data.get("project"), dict) else {}
        info["python"] = {
            "name": project.get("name"),
            "requires_python": project.get("requires-python"),
            "dependencies": project.get("dependencies", [])[:80] if isinstance(project.get("dependencies"), list) else [],
        }
        tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
        if "pytest" in tool or (root / "tests").exists():
            info["test_commands"].append("python -m pytest")

    requirements = root / "requirements.txt"
    if requirements.exists():
        info["files"].append("requirements.txt")
        deps = [
            line.strip()
            for line in requirements.read_text(errors="replace").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        info["dependencies"]["requirements.txt"] = deps[:80]

    for name in ("pnpm-lock.yaml", "package-lock.json", "yarn.lock", "uv.lock", "poetry.lock"):
        if (root / name).exists():
            info["files"].append(name)
    return info


def _key_files(files: list[Path], root: Path) -> dict[str, list[str]]:
    groups = {
        "docs": [],
        "tests": [],
        "configs": [],
        "entrypoints": [],
        "scripts": [],
    }
    for path in files:
        rel = _rel(path, root)
        name = path.name.lower()
        parts = {part.lower() for part in path.parts}
        if name.startswith("readme") or path.suffix.lower() == ".md":
            groups["docs"].append(rel)
        if "test" in name or "tests" in parts:
            groups["tests"].append(rel)
        if name in {"pyproject.toml", "package.json", "requirements.txt", "runtime.json", "models.json", "plugins.json"} or path.suffix.lower() in {".toml", ".yaml", ".yml"}:
            groups["configs"].append(rel)
        if name in {"run.sh", "start.sh", "localcomputer.py", "main.py", "app.py"}:
            groups["entrypoints"].append(rel)
        if path.suffix.lower() == ".sh" or "scripts" in parts:
            groups["scripts"].append(rel)
    return {key: values[:40] for key, values in groups.items()}


def _todos(files: list[Path], root: Path, limit: int = 80) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in files:
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if path.suffix.lower() in {".json", ".csv", ".tsv"}:
            continue
        try:
            lines = path.read_text(errors="replace").splitlines()
        except Exception:
            continue
        for lineno, line in enumerate(lines, start=1):
            match = TODO_RE.search(line)
            if not match:
                continue
            stripped = line.strip()
            suffix = path.suffix.lower()
            if suffix in {".py", ".sh", ".toml", ".yaml", ".yml"} and not stripped.startswith("#"):
                continue
            if suffix in {".js", ".jsx", ".ts", ".tsx", ".css"} and not stripped.startswith(("//", "/*", "*")):
                continue
            if suffix == ".html" and "<!--" not in stripped:
                continue
            items.append(
                {
                    "path": _rel(path, root),
                    "line": lineno,
                    "tag": match.group(1).upper(),
                    "text": (match.group(2) or line.strip())[:220],
                }
            )
            if len(items) >= limit:
                return items
    return items


def _git_info(root: Path) -> dict[str, Any]:
    top = _run(["git", "rev-parse", "--show-toplevel"], root)
    if not top["ok"]:
        return {"is_repo": False, "status": "not a git repository"}
    branch = _run(["git", "branch", "--show-current"], root)
    status = _run(["git", "status", "--short"], root)
    remote = _run(["git", "remote", "-v"], root)
    return {
        "is_repo": True,
        "root": top["stdout"],
        "branch": branch["stdout"],
        "dirty": bool(status["stdout"]),
        "status_short": status["stdout"].splitlines()[:80],
        "remotes": remote["stdout"].splitlines()[:12],
    }


def build_workspace_index(root: Path | None = None, *, write_cache: bool = True) -> dict[str, Any]:
    root = (root or workspace_root()).resolve()
    files = _files(root)
    suffix_counts = Counter(path.suffix.lower() or "[none]" for path in files)
    languages = Counter(LANG_BY_SUFFIX.get(path.suffix.lower(), "Other") for path in files)
    total_bytes = sum(path.stat().st_size for path in files if path.exists())
    manifests = _manifest_info(root)
    index = {
        "workspace_id": _workspace_id(root),
        "root": str(root),
        "indexed_at": _utc_now(),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "languages": dict(languages.most_common()),
        "suffixes": dict(suffix_counts.most_common(20)),
        "manifests": manifests,
        "key_files": _key_files(files, root),
        "todos": _todos(files, root),
        "git": _git_info(root),
    }
    if write_cache:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(root).write_text(json.dumps(index, indent=2) + "\n")
    return index


def load_cached_index(root: Path | None = None) -> dict[str, Any] | None:
    root = (root or workspace_root()).resolve()
    path = _cache_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def workspace_brief(index: dict[str, Any]) -> str:
    lines = [
        f"Workspace: `{index['root']}`",
        f"Indexed: `{index['indexed_at']}`",
        f"Files: `{index['file_count']}`; size: `{index['total_bytes']}` bytes",
        "",
        "Languages:",
    ]
    for lang, count in list(index.get("languages", {}).items())[:8]:
        lines.append(f"- {lang}: {count}")

    manifests = index.get("manifests", {})
    if manifests.get("files"):
        lines.extend(["", "Manifests:", *[f"- `{name}`" for name in manifests["files"]]])

    scripts = manifests.get("package_scripts", {})
    if scripts:
        lines.extend(["", "Package scripts:"])
        for name, command in list(scripts.items())[:12]:
            lines.append(f"- `{name}`: `{command}`")

    test_commands = manifests.get("test_commands", [])
    if test_commands:
        lines.extend(["", "Likely verification commands:", *[f"- `{cmd}`" for cmd in test_commands]])

    git = index.get("git", {})
    if git:
        lines.extend(["", "Git:"])
        if git.get("is_repo"):
            lines.append(f"- branch: `{git.get('branch') or '[detached/unknown]'}`")
            lines.append(f"- dirty: `{bool(git.get('dirty'))}`")
        else:
            lines.append("- not a git repository")

    todos = index.get("todos", [])
    if todos:
        lines.extend(["", f"TODO/FIXME markers ({len(todos)} indexed):"])
        for item in todos[:12]:
            lines.append(f"- `{item['path']}:{item['line']}` {item['tag']}: {item['text']}")

    return "\n".join(lines)


def _verification_commands(index: dict[str, Any]) -> list[str]:
    manifests = index.get("manifests", {})
    commands = list(manifests.get("test_commands") or [])
    key_files = index.get("key_files", {})
    if commands:
        return commands[:8]
    if any(str(path).endswith(".py") for path in key_files.get("scripts", [])):
        return ["python -m compileall scripts"]
    if "requirements.txt" in manifests.get("files", []) or "pyproject.toml" in manifests.get("files", []):
        return ["python -m py_compile <changed Python files>"]
    if "package.json" in manifests.get("files", []):
        scripts = manifests.get("package_scripts", {})
        if "build" in scripts:
            return ["npm run build"]
        if "test" in scripts:
            return ["npm test"]
    return []


def workspace_health_report(index: dict[str, Any]) -> str:
    """Render a deterministic, model-free project health report."""
    manifests = index.get("manifests", {})
    key_files = index.get("key_files", {})
    git = index.get("git", {})
    todos = index.get("todos", [])
    languages = index.get("languages", {})
    commands = _verification_commands(index)
    risks: list[str] = []

    if not git.get("is_repo"):
        risks.append("This folder is not a git repository, so Locus cannot inspect branches, commits, or dirty files.")
    elif git.get("dirty"):
        risks.append(f"Git has {len(git.get('status_short') or [])} changed path(s).")
    if not commands:
        risks.append("No obvious test, lint, build, or compile command was detected.")
    if not key_files.get("tests"):
        risks.append("No test files were detected in the indexed workspace.")
    if todos:
        risks.append(f"{len(todos)} TODO/FIXME/HACK marker(s) are indexed.")

    lines = [
        "Workspace Health",
        "",
        f"- Root: `{index.get('root')}`",
        f"- Indexed: `{index.get('indexed_at')}`",
        f"- Files: `{index.get('file_count')}`; size: `{index.get('total_bytes')}` bytes",
        "- Languages: "
        + (", ".join(f"{name} {count}" for name, count in list(languages.items())[:6]) if languages else "unknown"),
        "",
        "Project Shape",
    ]

    manifest_files = manifests.get("files") or []
    lines.append("- Manifests: " + (", ".join(f"`{name}`" for name in manifest_files) if manifest_files else "none detected"))
    entrypoints = key_files.get("entrypoints") or []
    lines.append("- Entrypoints: " + (", ".join(f"`{name}`" for name in entrypoints[:8]) if entrypoints else "none detected"))
    tests = key_files.get("tests") or []
    lines.append("- Tests: " + (", ".join(f"`{name}`" for name in tests[:8]) if tests else "none detected"))

    lines.extend(["", "Git"])
    if git.get("is_repo"):
        lines.append(f"- Branch: `{git.get('branch') or '[detached/unknown]'}`")
        lines.append(f"- Dirty: `{bool(git.get('dirty'))}`")
        for item in (git.get("status_short") or [])[:12]:
            lines.append(f"- `{item}`")
    else:
        lines.append("- Not a git repository")

    lines.extend(["", "Suggested Local Checks"])
    if commands:
        lines.extend(f"- `{command}`" for command in commands)
    else:
        lines.append("- Add or document a local verification command.")

    lines.extend(["", "Risks And Gaps"])
    if risks:
        lines.extend(f"- {risk}" for risk in risks[:12])
    else:
        lines.append("- No obvious local health gaps detected by the deterministic index.")

    if todos:
        lines.extend(["", "Top TODO/FIXME Markers"])
        for item in todos[:10]:
            lines.append(f"- `{item['path']}:{item['line']}` {item['tag']}: {item['text']}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Index the active workspace")
    parser.add_argument("--json", action="store_true", help="Print JSON index")
    parser.add_argument("--cached", action="store_true", help="Use cached index when available")
    args = parser.parse_args()
    index = load_cached_index() if args.cached else None
    if index is None:
        index = build_workspace_index()
    print(json.dumps(index, indent=2) if args.json else workspace_brief(index))


if __name__ == "__main__":
    main()
