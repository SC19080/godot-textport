# Godot TextPort — Godot 游戏文本提取与导入工具

[English](README.md) | **简体中文**

**面向 Godot Engine 游戏（Godot 3 & Godot 4）的离线文本导出 / 导入工具。**

[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)](#环境要求)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Godot TextPort **不会**向游戏注入代码，不挂钩运行时，也不会破坏可执行文件。它直接解析并读写 Godot 游戏的数据包（`.pck` 归档文件或内嵌在 `.exe` 中的 PCK 数据段）以及场景与脚本资源：

1. **导出 (`export`)**：扫描游戏，将所有剧情对话、卡牌/道具名、描述说明、UI 文本汇总导出为扁平 JSON (`{"原文": ""}`)、分卷 JSON 或 Markdown 表格。
2. **翻译**：使用你喜欢的方式翻译（大语言模型、机翻脚本、CAT 工具或人工编辑）。
3. **导入 (`import`)**：读取翻译结果，原地安全写回游戏数据包并自动重构偏移，同时自动生成带时间戳的完整备份。

重启游戏即可体验汉化后的效果。

> **独立开源项目。** 本工具仅供学习研究与本地化翻译使用，不包含任何游戏数据，请在合法拥有的游戏上使用。
>
> **要翻译 RPG Maker 游戏？** 请查看同目录下的 `rpgm-textport-mv-mz` 与 `rpgm-textport-vx-ace`。

---

## 核心特性

- **支持 Godot 3 与 Godot 4**：自动兼容 Godot 3 (PCK Format 1) 与 Godot 4 (PCK Format 2)。
- **支持内嵌 EXE 与独立 PCK**：无论是单文件版游戏（PCK 内嵌在 `.exe` 末尾）还是分体版（`Game.exe` + `Game.pck`）均可直接处理。
- **全格式支持**：
  - 二进制场景与资源 (`.scn`, `.res` / RSRC 格式，包括 `PackedStringArray` 剧情长文本)
  - 文本场景与资源 (`.tscn`, `.tres`)
  - GDScript 脚本文件 (`.gd` 中的文本字面量与提示文本)
- **零外部依赖**：仅使用 Python 标准库，无需 `pip install` 任何额外第三方包。
- **多种导出格式**：
  - 标准扁平 JSON (`{"原文": ""}`)：大模型翻译最友好的格式。
  - 分卷导出 (`--split N`)：自动切分为指定大小的分片，方便大模型批量上下文处理。
  - Markdown 表格 (`--format md`)：方便用表格编辑器或文档工具直观查看与编辑。
- **安全第一**：导入默认支持 `--dry-run` 预览，正式写入前自动创建时间戳备份 (`_godot_textport_backup/`)。
- **解包实用功能**：提供 `extract-pck` 指令，可将 PCK 游戏包全部解压到本地供调试与浏览。

---

## 环境要求

- **Python 3.8+**（无需安装任何外部库）。
- Windows、macOS 或 Linux。
- 对游戏目录具有写入权限。

已在 Windows 11 环境下使用 Python 3.14.6 针对 Godot 4 游戏（如 `RJ01223044_trial`）完成实机完整测试。

---

## 快速上手

打开命令行终端并进入 `godot-textport` 目录：

### 1. 确认游戏可被识别 (`info`)

```bat
python godot_textport.py info "D:\Games\MyGodotGame"
```

输出示例：
```text
[*] 目标类型: embedded_exe
[*] 主文件路径: D:\Games\MyGodotGame\Game.exe
[+] Godot 引擎版本: 4.1.0
[+] PCK 格式版本: 2 (内嵌在 EXE 中)
[+] 打包文件总数: 901
[+] 估算可翻译文本条数: 440
```

### 2. 导出待翻译文本 (`export`)

**标准导出为单 JSON 文件：**
```bat
python godot_textport.py export "D:\Games\MyGodotGame" -o strings.json
```

**导出为 Markdown 表格：**
```bat
python godot_textport.py export "D:\Games\MyGodotGame" -o strings.md --format md
```

**分卷导出（每卷 200 条，适合 LLM 批量输入）：**
```bat
python godot_textport.py export "D:\Games\MyGodotGame" -o out\ --split 200
```

### 3. 进行翻译

在导出的 `strings.json` 中，仅需将**值**填入译文即可，**不要修改键名**：

```json
{
  "オプション": "选项设置",
  "BGM音量": "背景音乐音量",
  "戻る": "返回",
  "【ミヅキ】\n「……そ、そんな、わ、私ッ」": "【美月】\n「……怎、怎么会，我……」"
}
```

> 提示：同目录下的 `translation_prompt.txt` 包含了专为 AI / LLM 设计的系统提示词，可直接复制使用。

### 4. 导入回游戏 (`import`)

**先进行试运行检查（不改动任何文件）：**
```bat
python godot_textport.py import "D:\Games\MyGodotGame" strings.json --dry-run
```

**正式应用翻译：**
```bat
python godot_textport.py import "D:\Games\MyGodotGame" strings.json
```

如果使用了分卷导出，直接传入分卷所在文件夹即可：
```bat
python godot_textport.py import "D:\Games\MyGodotGame" out\
```

导入成功后，直接双击运行游戏即可看到翻译后的文本。

---

## 命令行参数一览

### `info`
```bat
python godot_textport.py info <game_path>
```
查看指定游戏的目标类型、引擎版本、资源文件统计和估算文本条数。

### `export`
```bat
python godot_textport.py export <game_path> [选项]
```
- `-o, --output <path>`：指定输出文件或目录（默认：`strings.json`）。
- `--format <json|md|detailed>`：选择导出格式（默认：`json`）。
- `--split <N>`：按每卷最多 N 条文本拆分为多个分卷文件。
- `--all`：提取所有非空文本（默认仅提取含日文/汉字的文本）。
- `--min-len <N>`：设置提取字符串的最小长度（默认：1）。
- `--no-scripts`：跳过 GDScript (`.gd`) 脚本中的文本。
- `--no-scenes`：跳过场景与资源 (`.scn`, `.res`, `.tscn`, `.tres`) 中的文本。

### `import`
```bat
python godot_textport.py import <game_path> <translation_path> [选项]
```
- `--dry-run`：仅模拟匹配并显示统计信息，不写入任何修改。
- `--no-backup`：跳过自动备份（不推荐）。默认会在游戏目录下创建 `_godot_textport_backup/<时间戳>/` 备份。

### `extract-pck`
```bat
python godot_textport.py extract-pck <game_path> -o <out_dir>
```
解包游戏数据包中的所有文件到本地文件夹，方便查看游戏源码与美术资源。

---

## 常见问题与注意事项

1. **游戏运行出现字体缺失或方块？**
   Godot 游戏默认使用游戏内置打包的字体文件（如 `GenJyuuGothic` 等 CJK 字体）。大多数日式 Godot 游戏自带支持中日韩字符的通用字体。如果译文中含有极罕见的生僻字可能显示为缺字，尽量使用标准规范常用汉字。
2. **如何还原备份？**
   如果不满意汉化或想恢复原版，只需进入游戏目录下的 `_godot_textport_backup/<时间戳>/` 文件夹，将备份的可执行文件或 `.pck` 复制覆盖回游戏目录即可。
3. **占位符注意：**
   部分卡牌描述或系统提示包含变量占位符（如 `atk1`, `atk2`, `%s`, `%d` 等），翻译时请务必原样保留这些占位符，不要将其翻译或删除。

---

## 制作人员

- **项目作者：** 本仓库所有者。
- **实现与文档协助：** Antigravity 中的 Gemini 3.8 Flash。

本仓库在 **Antigravity 中的 Gemini 3.8 Flash** 的帮助下完成。

---

## 许可证

MIT。详见 [LICENSE](LICENSE)。

