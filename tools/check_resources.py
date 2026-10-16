"""Read shared resource availability; never inspect or control other jobs."""
import ctypes
import json
import shutil
import socket
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MemoryStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
        (name, ctypes.c_ulonglong)
        for name in ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")
    ]


def main():
    report = {"project": str(ROOT), "disk_free_gb": round(shutil.disk_usage(ROOT).free / 2**30, 1)}
    if hasattr(ctypes, "windll"):
        memory = MemoryStatus()
        memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            report["memory"] = {"total_gb": round(memory.total / 2**30, 1), "available_gb": round(memory.available / 2**30, 1)}
    report["ports"] = {}
    for port in (8793, 8794):
        with socket.socket() as listener:
            try:
                listener.bind(("127.0.0.1", port))
                report["ports"][str(port)] = "available"
            except OSError as error:
                report["ports"][str(port)] = "unavailable: " + str(error)
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu", "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
        report["gpu"] = result.stdout.strip() or "unavailable"
    except (OSError, subprocess.TimeoutExpired):
        report["gpu"] = "unavailable"
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
