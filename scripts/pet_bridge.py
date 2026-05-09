"""
pet_bridge.py - Ultra-lightweight CLI bridge to the pet daemon.
Usage:
    python pet_bridge.py thinking "正在思考..."
    python pet_bridge.py running "处理代码中..."
    python pet_bridge.py idle
    python pet_bridge.py status
    python pet_bridge.py context <used> <total>
"""
import os
import sys
import json
import ctypes
import urllib.request

DAEMON_URL = "http://127.0.0.1:19876"
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".claude", "pet_config.json")


def _find_claude_window():
    """找到 Claude Code 终端窗口。用 GetForegroundWindow——hook 触发时用户正在终端操作。"""
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            return hwnd
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if hwnd:
            return hwnd
    except Exception:
        pass
    return None


def _save_console_hwnd(config: dict) -> dict:
    # 只在首次写入或值变化时更新（UserPromptSubmit 时前景窗口最可靠）
    hwnd = _find_claude_window()
    if hwnd and hwnd != config.get("console_hwnd", 0):
        config["console_hwnd"] = hwnd
    return config


def _update_config(updates: dict):
    """Merge updates into pet_config.json."""
    try:
        config = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
        config.update(updates)
        config = _save_console_hwnd(config)
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def main():
    if len(sys.argv) < 2:
        print("Usage: python pet_bridge.py <state> [message]")
        print("       python pet_bridge.py context <used> <total>")
        print("       python pet_bridge.py status")
        sys.exit(1)

    action = sys.argv[1]
    msg = sys.argv[2] if len(sys.argv) > 2 else ""

    if action == "status":
        resp = urllib.request.urlopen(f"{DAEMON_URL}/status")
        print(resp.read().decode("utf-8"))
        return

    if action == "context":
        used = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        total = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        _update_config({"context_used": used, "context_total": total})
        print(f"context: {used}/{total}")
        return

    # 每次状态更新时同步保存控制台窗口句柄
    _update_config({})

    data = json.dumps({"state": action, "message": msg}).encode("utf-8")
    req = urllib.request.Request(
        f"{DAEMON_URL}/set",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req)
    result = json.loads(resp.read().decode("utf-8"))
    status = f"{result['state']}" + (f' "{result["message"]}"' if result.get("message") else "")
    print(status)


if __name__ == "__main__":
    main()
