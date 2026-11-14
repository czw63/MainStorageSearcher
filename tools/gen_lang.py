#!/usr/bin/env python3

import json
import re
import sys
from pathlib import Path

import yaml


ITEM_PREFIX = "item.minecraft."
BLOCK_PREFIX = "block.minecraft."


def load_minecraft_entries(source: Path) -> dict[str, str]:
    with source.open(encoding="utf-8") as source_file:
        language_data = json.load(source_file)

    entries: dict[str, str] = {}
    item_ids: set[str] = set()

    # Iterating once preserves the source JSON order while giving item entries
    # precedence when an item and block share the same bare id.
    for key, value in language_data.items():
        if key.startswith(ITEM_PREFIX):
            bare_id = key[len(ITEM_PREFIX):]
            entries[bare_id] = value
            item_ids.add(bare_id)
        elif key.startswith(BLOCK_PREFIX):
            bare_id = key[len(BLOCK_PREFIX):]
            if bare_id not in item_ids:
                entries.setdefault(bare_id, value)

    return entries


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: python3 {sys.argv[0]} <path/to/zh_cn.json>", file=sys.stderr)
        raise SystemExit(2)

    source = Path(sys.argv[1])
    lang_file = Path(__file__).resolve().parents[1] / "lang" / "zh_cn.yml"
    old_content = lang_file.read_bytes()
    marker = re.search(rb"^minecraft:\n", old_content, re.MULTILINE)
    if marker is None:
        raise RuntimeError(f"Top-level minecraft section not found in {lang_file}")

    entries = load_minecraft_entries(source)
    minecraft_yaml = yaml.safe_dump(
        {"minecraft": entries},
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        indent=2,
        width=1_000_000,
    ).encode("utf-8")

    new_content = old_content[:marker.start()] + minecraft_yaml
    lang_file.write_bytes(new_content)


if __name__ == "__main__":
    main()
