"""
pet_statusline.py - Claude Code statusline command that feeds context usage to the pet.
Reads JSON from stdin, extracts context_window fields, writes to pet_config.json.
Outputs a minimal statusline to stdout.
"""
import sys
import json
import os

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".claude", "pet_config.json")


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            print("pet")
            return
        data = json.loads(raw)
        cw = data.get("context_window", {})
        if not cw:
            print("pet")
            return

        # current_usage is a dict: {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}
        usage = cw.get("current_usage", 0)
        if isinstance(usage, dict):
            used = sum(v for v in usage.values())
        else:
            used = int(usage) if usage else 0

        total = cw.get("context_window_size", 0) or cw.get("total", 0)

        if total > 0:
            _write_context(used, total)
    except (json.JSONDecodeError, OSError):
        pass

    # Output a minimal statusline
    print("pet")


def _write_context(used: int, total: int):
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
    except OSError:
        pass


if __name__ == "__main__":
    main()
