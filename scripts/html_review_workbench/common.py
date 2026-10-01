"""Shared low-level helpers for Reviewable HTML Workbench modules."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
COMMENTS_SCHEMA_PATH = REPO_ROOT / "schemas" / "comments.schema.json"
MERMAID_INIT_JS = "mermaid.initialize({startOnLoad: true, theme: 'dark', securityLevel: 'strict'})"
PUBLISH_EXPORT_JS_PATH = REPO_ROOT / "templates" / "assets" / "publish-export.js"
PUBLISH_OVERRIDES_CSS_PATH = REPO_ROOT / "templates" / "assets" / "publish-overrides.css"
TASK_CHECKLIST_JS_PATH = REPO_ROOT / "templates" / "assets" / "task-checklist.js"
INTERACTIVE_STATE_JS_PATH = REPO_ROOT / "templates" / "assets" / "interactive-state.js"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _pid_is_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_is_alive_windows(pid: int) -> bool:
    # os.kill(pid, 0) is a POSIX idiom; on Windows it raises OSError
    # (WinError 87, invalid parameter) instead of probing liveness, so query
    # the process handle through the Win32 API via ctypes (stdlib, no deps).
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ERROR_ACCESS_DENIED = 5
    STILL_ACTIVE = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # The process exists but we lack access rights -> treat as alive,
        # mirroring the POSIX PermissionError branch above.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def write_json(path: Path, payload: dict[str, Any], *, ensure_parent: bool = False, indent: int | None = 2) -> None:
    """JSON を不可分に書く。

    # rhw-local-patch: atomic-write-json
    Path.write_text はファイルを 0 バイトに切り詰めてから書くので、書いている途中で
    UTF-8 への変換に失敗すると中身が全部消える（実害 2026-08-05: 対になっていない
    サロゲート半片を含むコメントで comments.json が 0 バイトになった）。
    ここでは先にバイト列まで作り、一時ファイルへ書いてから os.replace で差し替える。
    """
    if ensure_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    # 変換を書き込みより先に済ませる。ここで例外が出た時点では既存ファイルに触れていない。
    data = (json.dumps(payload, ensure_ascii=False, indent=indent) + "\n").encode("utf-8")
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def unique_path(path: Path, *, on_exhausted: Callable[[Path], Exception]) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise on_exhausted(path)


def resolve_bundle_json_path(
    root: Path,
    relative_path: str,
    *,
    label: str,
    error: Callable[[str], Exception],
) -> Path:
    if not relative_path:
        raise error(f"{label} path is required")
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise error(f"{label} path must be relative")
    if any(part == ".." for part in candidate.parts):
        raise error(f"{label} path must not contain parent traversal")

    resolved_root = root.resolve()
    resolved_path = (resolved_root / candidate).resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise error(f"{label} path must stay inside the bundle root")
    if resolved_path.suffix != ".json":
        raise error(f"{label} path must be a JSON file")
    return resolved_path
