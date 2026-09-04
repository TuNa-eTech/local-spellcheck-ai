#!/usr/bin/env python3
"""Build merged Vietnamese dictionary data files from 3 open-source repos.

Sources:
  - hunspell-vi (syllables + REP/MAP rules)
  - undertheseanlp/dictionary (syllables + compound words)
  - duyet/vietnamese-wordlist (syllables + compound words)

Usage:
    python build_dictionary.py
"""
from __future__ import annotations

import json
import unicodedata
import urllib.request
from pathlib import Path

HUNSPELL_DIC_URL = "https://raw.githubusercontent.com/1ec5/hunspell-vi/main/dictionaries/vi-DauMoi.dic"
HUNSPELL_AFF_URL = "https://raw.githubusercontent.com/1ec5/hunspell-vi/main/dictionaries/vi-DauMoi.aff"
UNDERTHESEA_URL = "https://raw.githubusercontent.com/undertheseanlp/dictionary/master/dictionary/words.txt"
VIET39K_URL = "https://raw.githubusercontent.com/duyet/vietnamese-wordlist/master/Viet39K.txt"

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "src" / "soatvan" / "checking" / "data"


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text).strip().casefold()


def _fetch(url: str) -> str:
    print(f"  Downloading {url.split('/')[-1]}...")
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8")


def _parse_hunspell_dic(content: str) -> set[str]:
    lines = content.strip().splitlines()
    # First line is word count
    return {_normalize(line.strip()) for line in lines[1:] if line.strip()}


def _parse_hunspell_aff(content: str) -> tuple[list[tuple[str, str]], dict[str, str]]:
    rep_rules: list[tuple[str, str]] = []
    tone_map: dict[str, str] = {}

    for line in content.splitlines():
        parts = line.strip().split()
        if len(parts) == 3 and parts[0] == "REP" and not parts[1].isdigit():
            rep_rules.append((parts[1], parts[2]))
        elif len(parts) == 2 and parts[0] == "MAP" and not parts[1].isdigit():
            chars = parts[1]
            # Skip compound MAP entries with parentheses
            if "(" in chars:
                continue
            # Only process simple hỏi↔ngã pairs (2 chars)
            if len(chars) == 2:
                tone_map[chars[0]] = chars[1]
                tone_map[chars[1]] = chars[0]

    return rep_rules, tone_map


def _parse_underthesea(content: str) -> tuple[set[str], set[str]]:
    syllables: set[str] = set()
    compounds: set[str] = set()
    for line in content.strip().splitlines():
        entry = json.loads(line)
        text = entry["text"]
        normalized = _normalize(text)
        if " " in normalized:
            compounds.add(normalized)
        # Extract individual syllables from all entries
        for part in normalized.split():
            if "-" not in part:
                syllables.add(part)
    return syllables, compounds


def _parse_viet39k(content: str) -> tuple[set[str], set[str]]:
    syllables: set[str] = set()
    compounds: set[str] = set()
    for line in content.strip().splitlines():
        text = line.strip()
        if not text:
            continue
        normalized = _normalize(text)
        if " " in normalized:
            compounds.add(normalized)
        for part in normalized.split():
            if "-" not in part:
                syllables.add(part)
    return syllables, compounds


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Building Vietnamese dictionary data files...")

    # 1. Download all sources
    print("\n[1/4] Downloading sources...")
    hunspell_dic = _fetch(HUNSPELL_DIC_URL)
    hunspell_aff = _fetch(HUNSPELL_AFF_URL)
    underthesea_data = _fetch(UNDERTHESEA_URL)
    viet39k_data = _fetch(VIET39K_URL)

    # 2. Parse all sources
    print("\n[2/4] Parsing sources...")
    hunspell_syllables = _parse_hunspell_dic(hunspell_dic)
    rep_rules, tone_map = _parse_hunspell_aff(hunspell_aff)
    underthesea_syllables, underthesea_compounds = _parse_underthesea(underthesea_data)
    viet39k_syllables, viet39k_compounds = _parse_viet39k(viet39k_data)

    print(f"  hunspell-vi syllables: {len(hunspell_syllables):,}")
    print(f"  undertheseanlp syllables: {len(underthesea_syllables):,}, compounds: {len(underthesea_compounds):,}")
    print(f"  Viet39K syllables: {len(viet39k_syllables):,}, compounds: {len(viet39k_compounds):,}")
    print(f"  REP rules: {len(rep_rules)}, tone MAP entries: {len(tone_map)}")

    # 3. Merge
    print("\n[3/4] Merging...")
    all_syllables = hunspell_syllables | underthesea_syllables | viet39k_syllables
    all_compounds = underthesea_compounds | viet39k_compounds

    print(f"  Total unique syllables: {len(all_syllables):,}")
    print(f"  Total unique compounds: {len(all_compounds):,}")

    # 4. Write output files
    print("\n[4/4] Writing output files...")

    syllables_path = OUTPUT_DIR / "syllables.txt"
    syllables_path.write_text(
        "\n".join(sorted(all_syllables)) + "\n", encoding="utf-8"
    )
    print(f"  {syllables_path.name}: {len(all_syllables):,} entries")

    compounds_path = OUTPUT_DIR / "compounds.txt"
    compounds_path.write_text(
        "\n".join(sorted(all_compounds)) + "\n", encoding="utf-8"
    )
    print(f"  {compounds_path.name}: {len(all_compounds):,} entries")

    rules_path = OUTPUT_DIR / "rep_rules.json"
    rules_data = {
        "replacements": [{"from": f, "to": t} for f, t in rep_rules],
        "tone_map": tone_map,
    }
    rules_path.write_text(
        json.dumps(rules_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"  {rules_path.name}: {len(rep_rules)} REP rules, {len(tone_map)} tone MAP entries")

    print("\nDone!")
    return


if __name__ == "__main__":
    main()
