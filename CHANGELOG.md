# Changelog

All notable changes to this project are documented in this file.

## 0.1.0 — 2026-11-07

- Initial release of **Godot TextPort**.
- Supports Godot 3 and Godot 4 engines.
- Supports games with embedded PCK in executables (`Game.exe`), standalone PCK archives (`Game.pck`), and loose project directories (`project.godot`).
- Extracts translatable strings from binary resources (`.res`, `.scn`), text scenes/resources (`.tscn`, `.tres`), and GDScript source files (`.gd`).
- Supports exporting to flat JSON (`{"original": ""}`), split JSON batches (`--split N`), Markdown table (`--format md`), and detailed JSON with context (`--format detailed`).
- Supports importing from JSON, split directory, or Markdown table.
- Automatic timestamped backups before writing.
- Safe `--dry-run` simulation mode.
- `extract-pck` command to unpack all files from PCK / embedded EXE.
- No injection, no runtime hooks, no network access, and zero external dependencies (Python standard library only).

