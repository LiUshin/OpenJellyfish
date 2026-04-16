"""
生成 JellyfishBot 注册码

用法:
    python generate_keys.py              # 生成 10 个注册码
    python generate_keys.py 20           # 生成 20 个注册码
    python generate_keys.py 5 --append   # 追加 5 个到已有文件
"""

import json
import os
import sys
import secrets

CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
REG_KEYS_FILE = os.path.join(CONFIG_DIR, "registration_keys.json")
EXAMPLE_FILE = os.path.join(CONFIG_DIR, "registration_keys.example.json")


def generate_key() -> str:
    part1 = secrets.token_hex(4).upper()
    part2 = secrets.token_hex(4).upper()
    return f"DA-{part1}-{part2}"


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 10
    append = "--append" in sys.argv

    os.makedirs(CONFIG_DIR, exist_ok=True)

    if append and os.path.exists(REG_KEYS_FILE):
        with open(REG_KEYS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    elif os.path.exists(REG_KEYS_FILE) and not append:
        print(f"[!] {REG_KEYS_FILE} 已存在。")
        print("    使用 --append 追加，或删除文件后重新生成。")
        sys.exit(1)
    else:
        data = {"description": "JellyfishBot 注册码 — 每个 key 只能使用一次", "keys": []}

    new_keys = []
    for _ in range(count):
        key = generate_key()
        data["keys"].append({
            "key": key,
            "used": False,
            "used_by": None,
            "used_at": None,
        })
        new_keys.append(key)

    with open(REG_KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"已生成 {count} 个注册码，保存到 {REG_KEYS_FILE}\n")
    for k in new_keys:
        print(f"  {k}")
    print(f"\n总计 {len(data['keys'])} 个注册码（含已有）。")


if __name__ == "__main__":
    main()
