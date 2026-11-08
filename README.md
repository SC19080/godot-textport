# Godot TextPort — Godot Game Text Extraction & Importing Tool

**English** | [简体中文](README.zh-CN.md)

**Offline text extractor / importer for Godot Engine games (Godot 3 & Godot 4).**

[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](#requirements)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Godot TextPort does **not** inject code into the game, hook the runtime, or modify executables in an invasive way. It reads and writes Godot game archives (`.pck` files or embedded PCK segments inside Windows `.exe` files) as well as scene and script resources directly:

1. **export**: Scans the game and extracts all dialogue, card/item names, descriptions, and UI text into a flat JSON file (`{"original": ""}`), split JSON files, or a Markdown table.
2. **translate**: Translate the values using your preferred method (LLM, CAT tools, machine translation, or manual editing).
3. **import**: Reads the translated file and writes it back into the game data safely, adjusting internal offsets and creating an automatic timestamped backup.

The game will display the translated text when restarted.

> **Independent project.** Godot TextPort is an independent tool for game localization and translation research. It does not include any game data. You must own the games you translate.
>
> **Translating an RPG Maker game?** See `rpgm-textport-mv-mz` and `rpgm-textport-vx-ace`.

---

## Key Features

- **Supports Godot 3 & Godot 4**: Compatible with Godot 3 (PCK Format 1) and Godot 4 (PCK Format 2).
- **Supports Embedded EXEs & Standalone PCKs**: Works seamlessly with single-file executables (where PCK is appended to the `.exe`) or split executables (`Game.exe` + `Game.pck`).
- **Comprehensive Format Support**:
  - Binary scenes and resources (`.scn`, `.res` / RSRC format, including multi-line narrative in `PackedStringArray`).
  - Text scenes and resources (`.tscn`, `.tres`).
  - GDScript source files (`.gd` string literals and messages).
- **Zero External Dependencies**: Pure Python standard library only. No `pip install` required.
- **Multiple Export Formats**:
  - Flat JSON (`{"original": ""}`): Optimized for LLM batch translation.
  - Split JSON (`--split N`): Automatically splits text into chunks for batch processing.
  - Markdown table (`--format md`): Formatted for visual inspection and tabular editing.
- **Safe by Default**: Supports `--dry-run` to preview replacements before writing. Automatically creates timestamped backups in `_godot_textport_backup/`.
- **Archive Extraction**: Includes an `extract-pck` command to unpack all files from the PCK archive to disk.

---

## Requirements

- **Python 3.8+** (uses only standard library modules).
- Windows, macOS, or Linux.
- Write permission to the game folder.

Tested on Windows 11 with Python 3.14.6 against Godot 4 games (such as `RJ01223044_trial`).

---

## Quick Start

Open a terminal in the folder containing `godot_textport.py`:

### 1. Check Game Detection (`info`)

```bat
python godot_textport.py info "C:\Games\MyGodotGame"
```

Expected output:
```text
[*] 目标类型: embedded_exe
[*] 主文件路径: C:\Games\MyGodotGame\Game.exe
[+] Godot 引擎版本: 4.1.0
[+] PCK 格式版本: 2 (内嵌在 EXE 中)
[+] 打包文件总数: 901
[+] 估算可翻译文本条数: 440
```

### 2. Export Text (`export`)

**Export to a single JSON file:**
```bat
python godot_textport.py export "C:\Games\MyGodotGame" -o strings.json
```

**Export to a Markdown table:**
```bat
python godot_textport.py export "C:\Games\MyGodotGame" -o strings.md --format md
```

**Split into batches (e.g., 200 entries per file for LLMs):**
```bat
python godot_textport.py export "C:\Games\MyGodotGame" -o out\ --split 200
```

### 3. Translate the Text

In `strings.json`, translate only the **values**. Do not modify the keys:

```json
{
  "オプション": "Options",
  "BGM音量": "BGM Volume",
  "戻る": "Back",
  "【ミヅキ】\n「……そ、そんな、わ、私ッ」": "【Mizuki】\n\"...N-No way, I...\""
}
```

> Tip: `translation_prompt.txt` in this repository provides a ready-to-use LLM system prompt.

### 4. Import the Translations (`import`)

**Simulate the import first without writing changes:**
```bat
python godot_textport.py import "C:\Games\MyGodotGame" strings.json --dry-run
```

**Apply the translations:**
```bat
python godot_textport.py import "C:\Games\MyGodotGame" strings.json
```

If you used `--split`, point the command to the output directory:
```bat
python godot_textport.py import "C:\Games\MyGodotGame" out\
```

After the import completes, run the game executable to verify the translations.

---

## CLI Reference

### `info`
```bat
python godot_textport.py info <game_path>
```
Displays target detection mode, engine version, file count, and estimated translatable strings.

### `export`
```bat
python godot_textport.py export <game_path> [options]
```
- `-o, --output <path>`: Output file or folder (default: `strings.json`).
- `--format <json|md|detailed>`: Output format (default: `json`).
- `--split <N>`: Split text into parts with up to N entries per file.
- `--all`: Extract all non-empty strings (default extracts only Japanese/CJK text).
- `--min-len <N>`: Minimum string length filter (default: 1).
- `--no-scripts`: Skip GDScript (`.gd`) files.
- `--no-scenes`: Skip scenes and resources (`.scn`, `.res`, `.tscn`, `.tres`).

### `import`
```bat
python godot_textport.py import <game_path> <translation_path> [options]
```
- `--dry-run`: Preview matches and statistics without modifying game files.
- `--no-backup`: Skip automatic backup creation (not recommended).

### `extract-pck`
```bat
python godot_textport.py extract-pck <game_path> -o <out_dir>
```
Unpacks all files from the PCK archive or embedded executable to a local folder.

---

## Credits

- **Project author:** the repository owner.
- **Implementation and documentation assistance:** Gemini 3.8 Flash in Antigravity.

This repository was prepared with the help of **Gemini 3.8 Flash in Antigravity**.

---

## License

MIT License. See [LICENSE](LICENSE) for details.

