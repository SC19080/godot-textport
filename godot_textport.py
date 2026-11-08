#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Godot TextPort
# Created with assistance from Gemini 3.8 Flash in Antigravity.
# Offline text extractor / importer for Godot Engine games (Godot 3 & 4)
# License: MIT
"""
Godot TextPort 0.1.0 —— Godot 游戏文本离线提取 / 导入工具

思路：不注入、不拦截运行时，直接读写 Godot 游戏的数据包（PCK / 内嵌 EXE）与场景/脚本资源文件。
    info   : 探测 Godot 游戏版本、包格式、包含的资源与可翻译文本数量
    export : 提取所有可翻译文本，导出为扁平 JSON、分卷 JSON 或 Markdown
    import : 读取翻译后的文本（"原文": "译文"），写回 PCK / EXE / 资源文件，并自动备份
    extract-pck : 解包 PCK / 内嵌 EXE 中的全部文件到目录
    build-pck   : 将目录重新打包为 PCK 或写回 EXE

用法：
    python godot_textport.py info   "D:\\Games\\MyGodotGame"
    python godot_textport.py export "D:\\Games\\MyGodotGame" -o strings.json
    python godot_textport.py export "D:\\Games\\MyGodotGame" -o strings.md --format md
    python godot_textport.py export "D:\\Games\\MyGodotGame" -o out\\strings.json --split 300
    python godot_textport.py import "D:\\Games\\MyGodotGame" strings.json --dry-run
    python godot_textport.py import "D:\\Games\\MyGodotGame" strings.json
    python godot_textport.py import "D:\\Games\\MyGodotGame" out\\
    python godot_textport.py extract-pck "D:\\Games\\MyGodotGame" -o unpacked\\
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import re
import shutil
import struct
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

VERSION = "0.1.0"
TOOL_NAME = "godot-textport"
BACKUP_DIR_NAME = "_godot_textport_backup"

# 文本过滤正则
JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff66-\uff9f]")
NUMERIC_RE = re.compile(r"^[\s\d\W_]+$", re.UNICODE)
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

PCK_MAGIC = b"GDPC"
RSRC_MAGIC = b"RSRC"


def setup_console() -> None:
    """Windows 控制台 UTF-8 支持"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def is_translatable(text: Any, require_cjk: bool = True, min_len: int = 1) -> bool:
    """判断字符串是否为游戏内可翻译文本"""
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if len(stripped) < min_len:
        return False
    # 排除 Godot 内部引用与路径
    if stripped.startswith(("res://", "user://", "local://", "uid://", "NodePath(", "ext_resource")):
        return False
    if stripped.endswith((".png", ".jpg", ".webp", ".ogg", ".mp3", ".wav", ".tscn", ".scn", ".tres", ".res", ".gd", ".shader", ".blend")):
        return False
    # 纯数字/标点/空白
    if NUMERIC_RE.match(stripped):
        return False
    # 二进制非法控制字符
    if CONTROL_CHARS_RE.search(text):
        return False
    if require_cjk:
        return bool(JAPANESE_RE.search(stripped))
    return True


# ==============================================================================
# Godot PCK 归档解析与构建
# ==============================================================================

class PCKFileEntry:
    def __init__(self, path: str, path_bytes: bytes, offset: int, size: int, md5: bytes, flags: int):
        self.path = path
        self.path_bytes = path_bytes
        self.offset = offset
        self.size = size
        self.md5 = md5
        self.flags = flags


class GodotPCK:
    def __init__(self):
        self.format_version: int = 2
        self.ver_major: int = 4
        self.ver_minor: int = 0
        self.ver_rev: int = 0
        self.flags: int = 0
        self.file_base: int = 0
        self.reserved: bytes = b"\x00" * 64
        self.entries: List[PCKFileEntry] = []
        self.entry_map: Dict[str, PCKFileEntry] = {}
        # 内嵌 EXE 信息
        self.is_embedded: bool = False
        self.exe_prefix_len: int = 0
        self.pck_data_len: int = 0

    @classmethod
    def from_stream(cls, f: io.BufferedReader, total_size: int) -> "GodotPCK":
        pck = cls()
        # 1. 检测文件尾部是否含有内嵌 PCK footer: [uint64 pck_len_without_footer, uint32 magic GDPC]
        if total_size >= 12:
            f.seek(total_size - 12)
            footer_data = f.read(12)
            pck_len, magic = struct.unpack("<QI", footer_data)
            if magic == 0x43504447:  # 'GDPC'
                pck.is_embedded = True
                pck.pck_data_len = pck_len
                pck.exe_prefix_len = total_size - 12 - pck_len
                f.seek(pck.exe_prefix_len)
            else:
                f.seek(0)
        else:
            f.seek(0)

        # 2. 读取 PCK Header
        magic = f.read(4)
        if magic != PCK_MAGIC:
            raise ValueError(f"不是有效的 Godot PCK 文件 (Magic: {magic!r})")

        pck.format_version = struct.unpack("<I", f.read(4))[0]
        pck.ver_major, pck.ver_minor, pck.ver_rev = struct.unpack("<3I", f.read(12))

        if pck.format_version >= 2:
            pck.flags = struct.unpack("<I", f.read(4))[0]
            pck.file_base = struct.unpack("<Q", f.read(8))[0]
            pck.reserved = f.read(64)
        else:
            # Godot 3 (Format 1)
            pck.flags = 0
            pck.file_base = 0
            pck.reserved = f.read(64)

        file_count = struct.unpack("<I", f.read(4))[0]

        # 3. 读取目录表
        for _ in range(file_count):
            plen = struct.unpack("<I", f.read(4))[0]
            pbytes = f.read(plen)
            path_str = pbytes.rstrip(b"\x00").decode("utf-8", errors="replace")
            offset, size = struct.unpack("<QQ", f.read(16))
            md5 = f.read(16)
            eflags = struct.unpack("<I", f.read(4))[0]

            entry = PCKFileEntry(path_str, pbytes, offset, size, md5, eflags)
            pck.entries.append(entry)
            pck.entry_map[path_str] = entry

        return pck

    def get_file_content(self, f: io.BufferedReader, entry: PCKFileEntry) -> bytes:
        if self.format_version >= 2:
            real_pos = self.file_base + entry.offset
        else:
            # Godot 3: offset is relative to PCK start (or file start if standalone)
            real_pos = self.exe_prefix_len + entry.offset
        f.seek(real_pos)
        return f.read(entry.size)


# ==============================================================================
# Godot 资源解析与文本替换 (RSRC, TSCN, TRES, GD)
# ==============================================================================

def patch_rsrc_data(raw_bytes: bytes, translations: Dict[str, str]) -> Tuple[bytes, int]:
    """替换 Godot 二进制资源 (RSRC) 中的文本，并自动调整 int_resources 偏移。"""
    if not raw_bytes.startswith(RSRC_MAGIC):
        return raw_bytes, 0

    raw = bytearray(raw_bytes)
    pos = 4
    # big_endian, use_real64, ver_maj, ver_min, ver_fmt
    pos += 20

    def read_str(buf: bytearray, p: int) -> Tuple[str, int]:
        slen = struct.unpack("<I", buf[p:p+4])[0]
        p += 4
        if slen == 0:
            return "", p
        s = buf[p:p+slen].rstrip(b"\x00").decode("utf-8", errors="replace")
        p += slen
        return s, p

    # 读取头部字段
    _, pos = read_str(raw, pos)
    importmd_ofs_pos = pos
    importmd_ofs = struct.unpack("<Q", raw[pos:pos+8])[0]
    pos += 8
    flags = struct.unpack("<I", raw[pos:pos+4])[0]
    pos += 4
    # uid
    pos += 8
    if flags & 8:  # FORMAT_FLAG_HAS_SCRIPT_CLASS
        _, pos = read_str(raw, pos)
    pos += 11 * 4  # RESERVED_FIELDS (11 个 uint32)

    # 字符串表
    scnt = struct.unpack("<I", raw[pos:pos+4])[0]
    pos += 4
    for _ in range(scnt):
        _, pos = read_str(raw, pos)

    # 外部资源表
    ecnt = struct.unpack("<I", raw[pos:pos+4])[0]
    pos += 4
    for _ in range(ecnt):
        _, pos = read_str(raw, pos)
        _, pos = read_str(raw, pos)
        pos += 8

    # 内部资源表
    icnt = struct.unpack("<I", raw[pos:pos+4])[0]
    pos += 4
    int_resources_meta = []
    for _ in range(icnt):
        ipath, pos = read_str(raw, pos)
        ofs_pos = pos
        iofs = struct.unpack("<Q", raw[pos:pos+8])[0]
        pos += 8
        int_resources_meta.append((ipath, ofs_pos, iofs))

    header_end = pos

    # 扫描数据段中所有长度前缀的有效字符串
    found_strings: List[Tuple[int, str, int]] = []
    i = header_end
    raw_len = len(raw)
    while i < raw_len - 8:
        slen = struct.unpack("<I", raw[i:i+4])[0]
        if 2 <= slen < 50000 and i + 4 + slen <= raw_len and raw[i + 4 + slen - 1] == 0:
            chunk = raw[i+4 : i+4+slen-1]
            try:
                s = chunk.decode("utf-8")
                if not CONTROL_CHARS_RE.search(s):
                    if s in translations and translations[s] != s:
                        found_strings.append((i, s, slen))
                        i += 4 + slen
                        continue
            except Exception:
                pass
        i += 1

    if not found_strings:
        return raw_bytes, 0

    # 倒序执行替换，避免前面的替换影响后续字符串在 bytearray 中的索引
    shifts: List[Tuple[int, int]] = []
    for str_pos, old_text, old_slen in reversed(found_strings):
        new_text = translations[old_text]
        new_bytes = new_text.encode("utf-8") + b"\x00"
        new_slen = len(new_bytes)
        new_prefix = struct.pack("<I", new_slen)
        delta = new_slen - old_slen
        shifts.append((str_pos, delta))
        raw[str_pos : str_pos + 4 + old_slen] = new_prefix + new_bytes

    # 更新内部资源偏移量
    for _, ofs_pos, old_iofs in int_resources_meta:
        total_delta = sum(delta for str_pos, delta in shifts if str_pos < old_iofs)
        new_iofs = old_iofs + total_delta
        raw[ofs_pos : ofs_pos + 8] = struct.pack("<Q", new_iofs)

    # 更新 importmd_ofs
    if importmd_ofs > 0:
        total_delta = sum(delta for str_pos, delta in shifts if str_pos < importmd_ofs)
        raw[importmd_ofs_pos : importmd_ofs_pos + 8] = struct.pack("<Q", importmd_ofs + total_delta)

    return bytes(raw), len(found_strings)


def extract_strings_from_rsrc(raw_bytes: bytes, require_cjk: bool = True, min_len: int = 1) -> List[str]:
    """从 Godot 二进制资源 (RSRC) 提取所有可翻译字符串"""
    if not raw_bytes.startswith(RSRC_MAGIC):
        return []
    res: List[str] = []
    raw_len = len(raw_bytes)
    i = 24
    while i < raw_len - 8:
        slen = struct.unpack("<I", raw_bytes[i:i+4])[0]
        if 2 <= slen < 50000 and i + 4 + slen <= raw_len and raw_bytes[i + 4 + slen - 1] == 0:
            chunk = raw_bytes[i+4 : i+4+slen-1]
            try:
                s = chunk.decode("utf-8")
                if is_translatable(s, require_cjk=require_cjk, min_len=min_len):
                    res.append(s)
                    i += 4 + slen
                    continue
            except Exception:
                pass
        i += 1
    return res


def extract_strings_from_gdscript(content: str, require_cjk: bool = True, min_len: int = 1) -> List[str]:
    """从 GDScript 源码中提取字符串字面量"""
    res: List[str] = []
    # 匹配三引号或普通单双引号字符串
    pattern = re.compile(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'|"([^"\\]*(?:\\.[^"\\]*)*)"|\'([^\'\\]*(?:\\.[^\'\\]*)*)\'', re.DOTALL)
    for m in pattern.finditer(content):
        s = m.group(1) or m.group(2) or m.group(3) or m.group(4)
        if s is not None:
            # 还原常见转义
            try:
                s_unesc = s.encode("raw_unicode_escape").decode("unicode_escape")
            except Exception:
                s_unesc = s
            if is_translatable(s_unesc, require_cjk=require_cjk, min_len=min_len):
                res.append(s_unesc)
    return res


def patch_gdscript(content: str, translations: Dict[str, str]) -> Tuple[str, int]:
    """替换 GDScript 源码中的字符串字面量"""
    count = 0
    pattern = re.compile(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'|"([^"\\]*(?:\\.[^"\\]*)*)"|\'([^\'\\]*(?:\\.[^\'\\]*)*)\'', re.DOTALL)

    def repl(m: re.Match) -> str:
        nonlocal count
        for idx in range(1, 5):
            val = m.group(idx)
            if val is not None:
                try:
                    unesc = val.encode("raw_unicode_escape").decode("unicode_escape")
                except Exception:
                    unesc = val
                if unesc in translations and translations[unesc] != unesc:
                    count += 1
                    new_val = translations[unesc]
                    # 转义回 GDScript 字符串
                    escaped = new_val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
                    return f'"{escaped}"'
        return m.group(0)

    patched = pattern.sub(repl, content)
    return patched, count


def extract_strings_from_tscn(content: str, require_cjk: bool = True, min_len: int = 1) -> List[str]:
    """从 TSCN / TRES 文本场景或文本资源中提取字符串"""
    res: List[str] = []
    pattern = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"')
    for m in pattern.finditer(content):
        s = m.group(1)
        try:
            s_unesc = s.encode("raw_unicode_escape").decode("unicode_escape")
        except Exception:
            s_unesc = s
        if is_translatable(s_unesc, require_cjk=require_cjk, min_len=min_len):
            res.append(s_unesc)
    return res


def patch_tscn(content: str, translations: Dict[str, str]) -> Tuple[str, int]:
    """替换 TSCN / TRES 文本资源中的字符串"""
    count = 0
    pattern = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"')

    def repl(m: re.Match) -> str:
        nonlocal count
        val = m.group(1)
        try:
            unesc = val.encode("raw_unicode_escape").decode("unicode_escape")
        except Exception:
            unesc = val
        if unesc in translations and translations[unesc] != unesc:
            count += 1
            new_val = translations[unesc]
            escaped = new_val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
            return f'"{escaped}"'
        return m.group(0)

    patched = pattern.sub(repl, content)
    return patched, count


# ==============================================================================
# 游戏目录与包定位 (Resolve Game Target)
# ==============================================================================

class GameTarget:
    def __init__(self, mode: str, game_dir: Path, main_path: Path):
        self.mode = mode  # 'embedded_exe', 'standalone_pck', 'loose_dir'
        self.game_dir = game_dir
        self.main_path = main_path


def resolve_game(path_str: str) -> GameTarget:
    """自动解析游戏路径（支持目录、.exe、.pck）"""
    p = Path(path_str).expanduser().resolve()
    if not p.exists():
        sys.exit(f"[x] 路径不存在: {p}")

    # 1. 传了单个文件
    if p.is_file():
        suffix = p.suffix.lower()
        if suffix == ".pck":
            return GameTarget("standalone_pck", p.parent, p)
        if suffix == ".exe":
            return GameTarget("embedded_exe", p.parent, p)
        sys.exit(f"[x] 不支持的文件类型: {p.name} (仅支持 .exe 或 .pck)")

    # 2. 传了目录：寻找 .exe 或 .pck 或 project.godot
    # 优先找同名 .pck 或包含 GDPC 的 .exe
    exes = list(p.glob("*.exe"))
    pcks = list(p.glob("*.pck"))

    # 如果有 .pck，优先判断
    if pcks:
        return GameTarget("standalone_pck", p, pcks[0])

    # 如果有 .exe，检测是否内嵌 PCK
    for exe in exes:
        size = exe.stat().st_size
        if size >= 12:
            try:
                with open(exe, "rb") as f:
                    f.seek(size - 12)
                    footer = f.read(12)
                    _, magic = struct.unpack("<QI", footer)
                    if magic == 0x43504447:
                        return GameTarget("embedded_exe", p, exe)
            except Exception:
                pass

    # 检查是否为散装项目目录 (含有 project.godot)
    if (p / "project.godot").exists() or (p / "project.binary").exists():
        return GameTarget("loose_dir", p, p)

    # 如果只有 exe 但没识别出 footer，默认当作 embedded_exe 尝试
    if exes:
        return GameTarget("embedded_exe", p, exes[0])

    sys.exit(f"[x] 无法在目录中找到 Godot 可执行文件或 PCK 数据包: {p}")


# ==============================================================================
# 命令实现：info
# ==============================================================================

def cmd_info(args: argparse.Namespace) -> None:
    target = resolve_game(args.game_path)
    print(f"[*] 目标类型: {target.mode}")
    print(f"[*] 主文件路径: {target.main_path}")

    if target.mode in ("embedded_exe", "standalone_pck"):
        size = target.main_path.stat().st_size
        with open(target.main_path, "rb") as f:
            pck = GodotPCK.from_stream(f, size)
            print(f"[+] Godot 引擎版本: {pck.ver_major}.{pck.ver_minor}.{pck.ver_rev}")
            print(f"[+] PCK 格式版本: {pck.format_version} ({'内嵌在 EXE 中' if pck.is_embedded else '独立 PCK'})")
            print(f"[+] 打包文件总数: {len(pck.entries)}")

            ext_stats: Dict[str, int] = {}
            total_translatable = 0
            for entry in pck.entries:
                ext = Path(entry.path).suffix.lower()
                ext_stats[ext] = ext_stats.get(ext, 0) + 1

                if ext in (".res", ".scn"):
                    content = pck.get_file_content(f, entry)
                    strings = extract_strings_from_rsrc(content)
                    total_translatable += len(strings)
                elif ext == ".gd":
                    content = pck.get_file_content(f, entry).decode("utf-8", errors="replace")
                    strings = extract_strings_from_gdscript(content)
                    total_translatable += len(strings)
                elif ext in (".tscn", ".tres"):
                    content = pck.get_file_content(f, entry).decode("utf-8", errors="replace")
                    strings = extract_strings_from_tscn(content)
                    total_translatable += len(strings)

            print(f"[+] 资源扩展名统计:")
            for ext, count in sorted(ext_stats.items(), key=lambda x: -x[1])[:10]:
                print(f"    {ext or '(none)'}: {count}")
            print(f"[+] 估算可翻译文本条数: {total_translatable}")
    else:
        # loose dir
        print("[+] 散装 Godot 项目目录")


# ==============================================================================
# 命令实现：export
# ==============================================================================

def cmd_export(args: argparse.Namespace) -> None:
    target = resolve_game(args.game_path)
    require_cjk = not args.all
    min_len = args.min_len

    extracted_dict: Dict[str, List[str]] = {}

    if target.mode in ("embedded_exe", "standalone_pck"):
        size = target.main_path.stat().st_size
        with open(target.main_path, "rb") as f:
            pck = GodotPCK.from_stream(f, size)
            print(f"[*] 正在扫描 PCK 资源 ({len(pck.entries)} 个文件)...")

            for entry in pck.entries:
                ext = Path(entry.path).suffix.lower()
                if ext in (".res", ".scn") and not args.no_scenes:
                    content = pck.get_file_content(f, entry)
                    strings = extract_strings_from_rsrc(content, require_cjk=require_cjk, min_len=min_len)
                    for s in strings:
                        extracted_dict.setdefault(s, []).append(f"{entry.path}")

                elif ext == ".gd" and not args.no_scripts:
                    content = pck.get_file_content(f, entry).decode("utf-8", errors="replace")
                    strings = extract_strings_from_gdscript(content, require_cjk=require_cjk, min_len=min_len)
                    for s in strings:
                        extracted_dict.setdefault(s, []).append(f"{entry.path} (script)")

                elif ext in (".tscn", ".tres") and not args.no_scenes:
                    content = pck.get_file_content(f, entry).decode("utf-8", errors="replace")
                    strings = extract_strings_from_tscn(content, require_cjk=require_cjk, min_len=min_len)
                    for s in strings:
                        extracted_dict.setdefault(s, []).append(f"{entry.path} (text scene)")
    else:
        # loose directory
        for root, _, files in os.walk(target.main_path):
            for file in files:
                p = Path(root) / file
                ext = p.suffix.lower()
                rel = p.relative_to(target.main_path).as_posix()
                if ext in (".res", ".scn") and not args.no_scenes:
                    content = p.read_bytes()
                    strings = extract_strings_from_rsrc(content, require_cjk=require_cjk, min_len=min_len)
                    for s in strings:
                        extracted_dict.setdefault(s, []).append(rel)
                elif ext == ".gd" and not args.no_scripts:
                    content = p.read_text(encoding="utf-8", errors="replace")
                    strings = extract_strings_from_gdscript(content, require_cjk=require_cjk, min_len=min_len)
                    for s in strings:
                        extracted_dict.setdefault(s, []).append(f"{rel} (script)")
                elif ext in (".tscn", ".tres") and not args.no_scenes:
                    content = p.read_text(encoding="utf-8", errors="replace")
                    strings = extract_strings_from_tscn(content, require_cjk=require_cjk, min_len=min_len)
                    for s in strings:
                        extracted_dict.setdefault(s, []).append(f"{rel} (text scene)")

    total_unique = len(extracted_dict)
    print(f"[+] 提取完成，共收集到 {total_unique} 条独立可翻译文本。")

    out_path = Path(args.output).expanduser().resolve()

    # 输出格式处理
    out_format = args.format.lower()
    if out_path.suffix.lower() == ".md":
        out_format = "md"

    if args.split and args.split > 0:
        # 分卷导出
        out_dir = out_path if out_path.is_dir() or not out_path.suffix else out_path.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        items = list(extracted_dict.items())
        total_parts = (len(items) + args.split - 1) // args.split
        print(f"[*] 正在分卷保存到 {out_dir}，共 {total_parts} 卷 (每卷最多 {args.split} 条)...")
        for idx in range(total_parts):
            chunk = items[idx * args.split : (idx + 1) * args.split]
            part_file = out_dir / f"part_{idx+1:03d}.json"
            chunk_dict = {orig: "" for orig, _ in chunk}
            with open(part_file, "w", encoding="utf-8") as f_out:
                json.dump(chunk_dict, f_out, ensure_ascii=False, indent=2)
        print(f"[+] 分卷导出成功: {out_dir}")
        return

    # 单文件导出
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_format == "md":
        # Markdown 表格形式导出
        lines = [
            f"# Godot 游戏翻译文本导出 ({target.main_path.name})",
            f"",
            f"共 {total_unique} 条文本。请在 `译文` 列中填写翻译结果，保留换行与占位符。",
            f"",
            f"| 原文 | 译文 | 出现位置 |",
            f"| --- | --- | --- |",
        ]
        for orig, locs in extracted_dict.items():
            loc_str = "<br>".join(locs[:3])
            if len(locs) > 3:
                loc_str += f"<br>... (+{len(locs)-3})"
            # 转义 markdown 表格中的管道符与换行符
            escaped_orig = orig.replace("|", "\\|").replace("\n", "<br>")
            lines.append(f"| {escaped_orig} | | {loc_str} |")

        out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[+] 文本已成功保存至 Markdown 文件: {out_path}")

    elif out_format == "detailed":
        # 详细 JSON 格式 (带位置上下文)
        detailed_list = [
            {"original": orig, "translation": "", "locations": locs}
            for orig, locs in extracted_dict.items()
        ]
        with open(out_path, "w", encoding="utf-8") as f_out:
            json.dump(detailed_list, f_out, ensure_ascii=False, indent=2)
        print(f"[+] 详细数据已成功保存至: {out_path}")

    else:
        # 标准扁平 JSON 格式 (完全兼容 rpgm-textport: {"原文": ""})
        flat_dict = {orig: "" for orig in extracted_dict.keys()}
        with open(out_path, "w", encoding="utf-8") as f_out:
            json.dump(flat_dict, f_out, ensure_ascii=False, indent=2)
        print(f"[+] 文本已成功保存至 JSON 文件: {out_path}")


# ==============================================================================
# 命令实现：import
# ==============================================================================

def load_translations_from_path(trans_path: Path) -> Dict[str, str]:
    """读取用户翻译文件，兼容多种输入格式（单 JSON、目录分卷 JSON、Markdown）"""
    res: Dict[str, str] = {}

    def parse_single_file(p: Path) -> None:
        if p.suffix.lower() == ".json":
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, dict):
                    # 检查是否有 {"strings": {...}} 外壳
                    if "strings" in data and isinstance(data["strings"], dict):
                        data = data["strings"]
                    for k, v in data.items():
                        if isinstance(v, str) and v.strip() and v != k:
                            res[k] = v
                elif isinstance(data, list):
                    # 兼容 [{"original": "...", "translation": "..."}, ...]
                    # 或 [{"text": "...", "translation": "..."}, ...]
                    for item in data:
                        if isinstance(item, dict):
                            orig = item.get("original") or item.get("text")
                            trans = item.get("translation")
                            if orig and trans and trans.strip() and trans != orig:
                                res[orig] = trans
            except Exception as e:
                print(f"[!] 读取 JSON 文件失败 {p.name}: {e}")

        elif p.suffix.lower() == ".md":
            # 解析 Markdown 表格
            content = p.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("|") and line.endswith("|"):
                    parts = [pt.strip() for pt in line[1:-1].split("|")]
                    if len(parts) >= 2 and parts[0] != "---" and parts[0] != "原文":
                        orig = parts[0].replace("\\|", "|").replace("<br>", "\n")
                        trans = parts[1].replace("\\|", "|").replace("<br>", "\n")
                        if orig and trans and trans != orig:
                            res[orig] = trans

    if trans_path.is_file():
        parse_single_file(trans_path)
    elif trans_path.is_dir():
        for f in sorted(trans_path.glob("*.json")):
            parse_single_file(f)
        for f in sorted(trans_path.glob("*.md")):
            parse_single_file(f)

    return res


def cmd_import(args: argparse.Namespace) -> None:
    target = resolve_game(args.game_path)
    trans_path = Path(args.translation_path).expanduser().resolve()
    if not trans_path.exists():
        sys.exit(f"[x] 翻译文件或目录不存在: {trans_path}")

    translations = load_translations_from_path(trans_path)
    if not translations:
        sys.exit(f"[x] 未能从 {trans_path} 中找到有效的翻译条目（需要非空且与原文不同的译文）。")

    print(f"[+] 已加载 {len(translations)} 条有效翻译。")

    if args.dry_run:
        print("[*] 正在执行试运行 (--dry-run)，不会修改任何游戏文件...")

    if target.mode in ("embedded_exe", "standalone_pck"):
        size = target.main_path.stat().st_size
        with open(target.main_path, "rb") as f:
            pck = GodotPCK.from_stream(f, size)
            if pck.is_embedded:
                f.seek(0)
                exe_prefix = f.read(pck.exe_prefix_len)
            else:
                exe_prefix = b""

            # 遍历并处理所有文件
            modified_files: Dict[str, bytes] = {}
            total_matches = 0

            for entry in pck.entries:
                ext = Path(entry.path).suffix.lower()
                if ext in (".res", ".scn"):
                    raw = pck.get_file_content(f, entry)
                    patched, count = patch_rsrc_data(raw, translations)
                    if count > 0:
                        modified_files[entry.path] = patched
                        total_matches += count
                elif ext == ".gd":
                    content = pck.get_file_content(f, entry).decode("utf-8", errors="replace")
                    patched_str, count = patch_gdscript(content, translations)
                    if count > 0:
                        modified_files[entry.path] = patched_str.encode("utf-8")
                        total_matches += count
                elif ext in (".tscn", ".tres"):
                    content = pck.get_file_content(f, entry).decode("utf-8", errors="replace")
                    patched_str, count = patch_tscn(content, translations)
                    if count > 0:
                        modified_files[entry.path] = patched_str.encode("utf-8")
                        total_matches += count

            print(f"[+] 匹配并准备替换 {total_matches} 处文本，涉及 {len(modified_files)} 个资源文件。")

            if args.dry_run:
                print("[*] 试运行结束，未写入任何更改。")
                return

            if not modified_files:
                print("[!] 没有匹配到任何需要写入的文件。")
                return

            # 创建自动备份
            if not args.no_backup:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_dir = target.game_dir / BACKUP_DIR_NAME / ts
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup_file = backup_dir / target.main_path.name
                print(f"[*] 正在备份原文件到: {backup_file}")
                shutil.copy2(target.main_path, backup_file)

            # 重建 PCK / EXE
            print(f"[*] 正在重构并写回数据包: {target.main_path.name} ...")
            temp_output = target.main_path.with_name(f"{target.main_path.name}.tmp")

            with open(temp_output, "wb") as out_f:
                if pck.is_embedded:
                    out_f.write(exe_prefix)
                pck_start_offset = out_f.tell()

                # 占位 PCK 头部 (100 字节)
                out_f.write(b"\x00" * 100)

                # 占位目录表
                dir_meta_records = []
                for entry in pck.entries:
                    pbytes = entry.path_bytes
                    out_f.write(struct.pack("<I", len(pbytes)))
                    out_f.write(pbytes)
                    meta_pos = out_f.tell()
                    out_f.write(b"\x00" * 36)
                    dir_meta_records.append((meta_pos, entry))

                # 对齐 file_base 到 16 字节
                dir_end = out_f.tell()
                pad_len = (16 - (dir_end % 16)) % 16
                out_f.write(b"\x00" * pad_len)
                file_base_abs = out_f.tell()

                # 写入文件数据并回填目录元数据
                current_off = 0
                for meta_pos, entry in dir_meta_records:
                    if entry.path in modified_files:
                        data = modified_files[entry.path]
                    else:
                        data = pck.get_file_content(f, entry)

                    entry_off = current_off
                    entry_sz = len(data)
                    entry_md5 = hashlib.md5(data).digest()

                    cur_pos = out_f.tell()
                    out_f.seek(meta_pos)
                    out_f.write(struct.pack("<QQ", entry_off, entry_sz))
                    out_f.write(entry_md5)
                    out_f.write(struct.pack("<I", entry.flags))
                    out_f.seek(cur_pos)

                    out_f.write(data)
                    current_off += entry_sz

                # 回填 PCK Header
                end_of_pck_data = out_f.tell()
                total_pck_data_len = end_of_pck_data - pck_start_offset

                out_f.seek(pck_start_offset)
                out_f.write(PCK_MAGIC)
                out_f.write(struct.pack("<4I", pck.format_version, pck.ver_major, pck.ver_minor, pck.ver_rev))
                if pck.format_version >= 2:
                    out_f.write(struct.pack("<I", pck.flags))
                    out_f.write(struct.pack("<Q", file_base_abs))
                    out_f.write(pck.reserved)
                else:
                    out_f.write(pck.reserved)
                out_f.write(struct.pack("<I", len(pck.entries)))

                # 如果是内嵌 EXE，追加 12 字节尾部: [uint64 pck_len_without_footer, uint32 magic GDPC]
                if pck.is_embedded:
                    out_f.seek(end_of_pck_data)
                    out_f.write(struct.pack("<QI", total_pck_data_len, 0x43504447))

            # 用临时文件原子替换目标文件
            shutil.move(str(temp_output), str(target.main_path))
            print(f"[+] 导入成功！已更新 {target.main_path.name}，可直接运行游戏查看汉化效果。")

    else:
        # loose dir
        # 创建自动备份
        if not args.dry_run and not args.no_backup:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_dir = target.game_dir / BACKUP_DIR_NAME / ts
            print(f"[*] 备份散装项目到: {backup_dir}")

        total_matches = 0
        modified_count = 0
        for root, _, files in os.walk(target.main_path):
            for file in files:
                p = Path(root) / file
                ext = p.suffix.lower()
                if ext in (".res", ".scn"):
                    content = p.read_bytes()
                    patched, count = patch_rsrc_data(content, translations)
                    if count > 0:
                        total_matches += count
                        modified_count += 1
                        if not args.dry_run:
                            p.write_bytes(patched)
                elif ext == ".gd":
                    content = p.read_text(encoding="utf-8", errors="replace")
                    patched, count = patch_gdscript(content, translations)
                    if count > 0:
                        total_matches += count
                        modified_count += 1
                        if not args.dry_run:
                            p.write_text(patched, encoding="utf-8")
                elif ext in (".tscn", ".tres"):
                    content = p.read_text(encoding="utf-8", errors="replace")
                    patched, count = patch_tscn(content, translations)
                    if count > 0:
                        total_matches += count
                        modified_count += 1
                        if not args.dry_run:
                            p.write_text(patched, encoding="utf-8")

        print(f"[+] 扫描完成：共匹配 {total_matches} 处，涉及 {modified_count} 个文件。")
        if args.dry_run:
            print("[*] 试运行结束，未写入任何文件。")
        else:
            print("[+] 文件替换完成！")


# ==============================================================================
# 命令实现：extract-pck
# ==============================================================================

def cmd_extract_pck(args: argparse.Namespace) -> None:
    target = resolve_game(args.game_path)
    if target.mode not in ("embedded_exe", "standalone_pck"):
        sys.exit("[x] 该命令仅支持从 .pck 或内嵌 EXE 中解包文件。")

    out_dir = Path(args.output).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    size = target.main_path.stat().st_size
    with open(target.main_path, "rb") as f:
        pck = GodotPCK.from_stream(f, size)
        print(f"[*] 正在从 {target.main_path.name} 解包 {len(pck.entries)} 个文件到 {out_dir} ...")
        for idx, entry in enumerate(pck.entries, 1):
            clean_rel = entry.path.replace("res://", "").lstrip("/\\")
            dst_file = out_dir / clean_rel
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            data = pck.get_file_content(f, entry)
            dst_file.write_bytes(data)
            if idx % 100 == 0 or idx == len(pck.entries):
                print(f"    [{idx}/{len(pck.entries)}] {clean_rel}")

    print(f"[+] 解包完成: {out_dir}")


# ==============================================================================
# 主入口与命令行参数
# ==============================================================================

def main() -> None:
    setup_console()
    parser = argparse.ArgumentParser(
        prog="godot_textport",
        description="Godot TextPort —— Offline text extractor and importer for Godot Engine games.",
    )
    parser.add_argument("--version", action="version", version=f"{TOOL_NAME} {VERSION}")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # 1. info
    p_info = subparsers.add_parser("info", help="探测 Godot 游戏包信息与可翻译文本数量")
    p_info.add_argument("game_path", help="游戏主目录、.exe 文件或 .pck 文件路径")
    p_info.set_defaults(func=cmd_info)

    # 2. export
    p_export = subparsers.add_parser("export", help="提取游戏中的全部可翻译文本")
    p_export.add_argument("game_path", help="游戏主目录、.exe 文件或 .pck 文件路径")
    p_export.add_argument("-o", "--output", default="strings.json", help="输出文件或目录 (默认: strings.json)")
    p_export.add_argument("--format", choices=["json", "md", "detailed"], default="json", help="输出格式: json, md, detailed (默认: json)")
    p_export.add_argument("--split", type=int, default=0, help="每卷最大文本条数 (0 表示不分卷)")
    p_export.add_argument("--all", action="store_true", help="提取所有文本 (默认仅提取日文/汉字)")
    p_export.add_argument("--min-len", type=int, default=1, help="提取的最小字符串长度 (默认: 1)")
    p_export.add_argument("--no-scripts", action="store_true", help="跳过 .gd 脚本文件的文本提取")
    p_export.add_argument("--no-scenes", action="store_true", help="跳过场景与资源文件 (.scn/.res/.tscn/.tres)")
    p_export.set_defaults(func=cmd_export)

    # 3. import
    p_import = subparsers.add_parser("import", help="将翻译好的文本导入回游戏")
    p_import.add_argument("game_path", help="游戏主目录、.exe 文件或 .pck 文件路径")
    p_import.add_argument("translation_path", help="翻译后的 JSON、MD 文件或分卷目录")
    p_import.add_argument("--dry-run", action="store_true", help="仅预览替换数量，不实际修改游戏文件")
    p_import.add_argument("--no-backup", action="store_true", help="不创建自动备份 (不推荐)")
    p_import.set_defaults(func=cmd_import)

    # 4. extract-pck
    p_extract = subparsers.add_parser("extract-pck", help="从 PCK 或内嵌 EXE 中解包文件到本地目录")
    p_extract.add_argument("game_path", help="游戏主目录、.exe 文件或 .pck 文件路径")
    p_extract.add_argument("-o", "--output", required=True, help="解包目标输出目录")
    p_extract.set_defaults(func=cmd_extract_pck)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

