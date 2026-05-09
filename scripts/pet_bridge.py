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
import urllib.request

DAEMON_URL = "http://127.0.0.1:19876"
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".claude", "pet_config.json")


def _update_context(used: int, total: int):
    """Write context usage to pet_config.json so the pet bar picks it up."""
    try:
        config = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
        config["context_used"] = used
        config["context_total"] = total
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"context: {used}/{total}")
    except OSError as e:
        print(f"Failed to update context: {e}", file=sys.stderr)


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
        _update_context(used, total)
        return

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
