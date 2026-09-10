"""Bounded, explicit local SteamVR log collection; never touches VPN settings."""

from __future__ import annotations

from pathlib import Path
import re
import sys


LOG_NAMES = ("vrserver.txt", "vrcompositor.txt", "vrmonitor.txt", "vrclient_steamtours.txt")
TAIL_BYTES = 64 * 1024
TAIL_LINES = 100


def redact(value: str, *, token: str = "") -> str:
    """Best-effort redaction, not a promise that third-party logs are anonymous."""
    value = value.replace(token, "[redacted]") if token else value
    value = re.sub(r"(?i)(bearer\s+)[^\s\"']+", r"\1[redacted]", value)
    value = re.sub(r"(?i)((?:[\w-]*(?:token|password|api[_-]?key|secret))[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]+", r"\1[redacted]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted]", value)
    value = re.sub(r"(?i)([A-Z]:\\Users\\)[^\\\s]+", r"\1[user]", value)
    value = re.sub(r"/Users/[^/\s]+", "/Users/[user]", value)
    return "".join(c for c in value if c >= " " or c in "\n\t")


def steam_logs_directory() -> Path | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "SteamPath")
        path = Path(value)
        if path.is_absolute():
            return path / "logs"
    except (OSError, TypeError):
        pass
    return None


def collect_runtime_logs(*, token: str = "") -> dict[str, object]:
    if sys.platform != "win32":
        return {"status": "windows_only", "files": [], "note": "SteamVR 日志请在 Windows 实机收集。"}
    directory = steam_logs_directory()
    if directory is None or not directory.is_dir():
        return {"status": "not_found", "files": [], "note": "未找到 Steam 日志目录；请从 SteamVR 菜单另存 System Report。"}
    return read_log_tails(directory, token=token)


def read_log_tails(directory: Path, *, token: str = "") -> dict[str, object]:
    """Read only fixed names directly beneath the discovered Steam logs folder."""
    root = directory.resolve()
    files = []
    for name in LOG_NAMES:
        path = root / name
        record: dict[str, object] = {"name": name, "status": "missing", "lines": []}
        try:
            if path.is_symlink() or path.resolve().parent != root:
                record["status"] = "unsafe_link_skipped"
            elif path.is_file():
                with path.open("rb") as stream:
                    stream.seek(0, 2)
                    start = max(0, stream.tell() - TAIL_BYTES)
                    stream.seek(start)
                    raw = stream.read(TAIL_BYTES)
                lines = raw.decode("utf-8", errors="replace").splitlines()
                if start and lines:
                    lines = lines[1:]
                record.update(status="collected", lines=[redact(line, token=token)[:1200] for line in lines[-TAIL_LINES:]])
        except OSError:
            record["status"] = "unreadable"
        files.append(record)
    return {"status": "collected", "files": files, "note": "仅末尾日志，已尽力脱敏；分享前请人工检查。文件缺失不代表 Home 未运行。"}
