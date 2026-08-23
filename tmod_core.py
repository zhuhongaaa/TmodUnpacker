"""
tmod_core.py
============
tModLoader .tmod 文件解析与解包核心库。

支持格式:
  - 新版 (tModLoader version >= 0.11):TMOD + 版本字符串 + SHA1(20) + 签名(256)
    + 数据长度 + 模组名 + 模组版本 + 文件数 + 文件表(名/未压缩/压缩) + 逐文件数据。
    每个文件独立存储,若 未压缩长度 != 压缩长度 则该文件为 raw DEFLATE 压缩。
  - 旧版 (version < 0.11):头部之后整个数据块为单个 DEFLATE 流,解压后内含
    模组名 + 模组版本 + 文件数 + (文件名 + 文件长度 + 文件原始数据)。

参考: tModLoader 源码 patches/tModLoader/Terraria/ModLoader/Core/TmodFile.cs
"""

from __future__ import annotations

import hashlib
import os
import struct
import zlib
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

try:
    from PIL import Image  # 可选: rawimg -> PNG 转换
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

# 日志回调: (level, message)。level ∈ {"INFO","WARN","ERROR"}
LogFn = Callable[[str, str], None]
# 进度回调: (当前文件序号, 文件总数, 当前文件名)
ProgressFn = Callable[[int, int, str], None]


class TmodError(Exception):
    """.tmod 文件解析/解包错误。"""


@dataclass
class FileEntry:
    name: str          # 内部路径(正斜杠)
    size: int          # 未压缩字节数
    compressed: int    # 存储字节数(等于 size 表示未压缩)


@dataclass
class TmodInfo:
    path: str
    tmod_version: str
    mod_name: str
    mod_version: str
    file_count: int
    sha1: bytes
    signed: bool
    data_length: int
    files: List[FileEntry] = field(default_factory=list)
    hash_verified: Optional[bool] = None
    is_legacy: bool = False  # 旧版(整体压缩)格式


# ---------------------------------------------------------------------------
# 基础读取工具
# ---------------------------------------------------------------------------

def _read_7bit_string(data: bytes, off: int) -> Tuple[str, int]:
    """读取 BinaryReader 风格的 7 位编码长度前缀 UTF-8 字符串。"""
    length = 0
    shift = 0
    while True:
        if off >= len(data):
            raise TmodError("读取字符串时越界,文件可能已损坏")
        b = data[off]
        off += 1
        length |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            break
        shift += 7
        if shift > 35:
            raise TmodError("字符串长度编码异常,文件可能已损坏")
    try:
        s = data[off:off + length].decode("utf-8")
    except UnicodeDecodeError as e:
        raise TmodError(f"字符串 UTF-8 解码失败: {e}") from e
    return s, off + length


def _parse_version_tuple(version: str) -> Tuple[int, ...]:
    parts = []
    for p in version.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


# ---------------------------------------------------------------------------
# 解析(仅读取头部与文件表,不解包数据)
# ---------------------------------------------------------------------------

def parse_tmod(path: str) -> TmodInfo:
    """解析 .tmod 文件头与文件表,返回元信息。不提取文件数据。"""
    with open(path, "rb") as f:
        data = f.read()

    if len(data) < 8:
        raise TmodError("文件过小,不是有效的 .tmod 文件")

    if data[:4] != b"TMOD":
        raise TmodError(f'无效的文件头: {data[:4]!r},应为 b"TMOD"')

    off = 4
    tmod_version, off = _read_7bit_string(data, off)

    # SHA1 哈希(20 字节)与签名(256 字节)
    if off + 20 + 256 > len(data):
        raise TmodError("文件头不完整(缺少哈希/签名)")
    sha1 = data[off:off + 20]
    off += 20
    signature = data[off:off + 256]
    off += 256
    signed = any(b != 0 for b in signature)

    if off + 4 > len(data):
        raise TmodError("文件头不完整(缺少数据长度)")
    data_length = struct.unpack_from("<i", data, off)[0]
    off += 4
    hash_start = off  # 哈希覆盖区域的起始

    # 数据长度应等于剩余字节数
    expected_remaining = len(data) - hash_start
    if data_length != expected_remaining:
        # 旧版/损坏都可能出现,仅记录,继续尝试解析
        pass

    version_tuple = _parse_version_tuple(tmod_version)
    is_legacy = version_tuple < (0, 11)

    if is_legacy:
        # 旧版:整个数据块是单个 DEFLATE 流
        try:
            blob = zlib.decompress(data[hash_start:], -15)
        except zlib.error as e:
            raise TmodError(f"旧版数据块解压失败: {e}") from e
        bo = 0
        mod_name, bo = _read_7bit_string(blob, bo)
        mod_version, bo = _read_7bit_string(blob, bo)
        count = struct.unpack_from("<i", blob, bo)[0]
        bo += 4
        files: List[FileEntry] = []
        for _ in range(count):
            fname, bo = _read_7bit_string(blob, bo)
            fsize = struct.unpack_from("<i", blob, bo)[0]
            bo += 4
            files.append(FileEntry(fname, fsize, fsize))
        info = TmodInfo(
            path=path, tmod_version=tmod_version, mod_name=mod_name,
            mod_version=mod_version, file_count=count, sha1=sha1,
            signed=signed, data_length=data_length, files=files,
            is_legacy=True,
        )
    else:
        # 新版:明文文件表 + 逐文件数据
        mod_name, off = _read_7bit_string(data, off)
        mod_version, off = _read_7bit_string(data, off)
        if off + 4 > len(data):
            raise TmodError("文件头不完整(缺少文件数)")
        count = struct.unpack_from("<i", data, off)[0]
        off += 4
        files = []
        for i in range(count):
            fname, off = _read_7bit_string(data, off)
            if off + 8 > len(data):
                raise TmodError(f"文件表第 {i} 项不完整")
            usize = struct.unpack_from("<i", data, off)[0]
            off += 4
            csize = struct.unpack_from("<i", data, off)[0]
            off += 4
            if csize < 0 or usize < 0:
                raise TmodError(f"文件 {fname} 的长度字段为负,文件可能已损坏")
            files.append(FileEntry(fname, usize, csize))
        info = TmodInfo(
            path=path, tmod_version=tmod_version, mod_name=mod_name,
            mod_version=mod_version, file_count=count, sha1=sha1,
            signed=signed, data_length=data_length, files=files,
            is_legacy=False,
        )

    # 校验哈希(SHA1 覆盖 hash_start 到文件末尾)
    try:
        calc = hashlib.sha1(data[hash_start:]).digest()
        info.hash_verified = (calc == sha1)
    except Exception:
        info.hash_verified = None

    return info


# ---------------------------------------------------------------------------
# 解包
# ---------------------------------------------------------------------------

def _safe_join(base: str, rel: str) -> str:
    """将内部相对路径安全拼接到输出目录,防止路径穿越。"""
    rel = rel.replace("\\", "/").lstrip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
    return os.path.join(base, *parts)


def _decompress(raw: bytes, expected: int) -> bytes:
    """解压单个文件数据(先试 raw DEFLATE,失败再试 zlib 包装)。"""
    try:
        out = zlib.decompress(raw, -15)
    except zlib.error:
        try:
            out = zlib.decompress(raw)
        except zlib.error as e:
            raise TmodError(f"DEFLATE 解压失败: {e}") from e
    if len(out) != expected:
        raise TmodError(f"解压后长度不符: 期望 {expected}, 实际 {len(out)}")
    return out


def _rawimg_to_png(data: bytes) -> bytes:
    """把 tModLoader .rawimg 转为 PNG 字节。格式: int ver + int w + int h + RGBA。"""
    if not _HAS_PIL:
        raise TmodError("未安装 Pillow,无法转换 rawimg")
    if len(data) < 12:
        raise TmodError("rawimg 数据过小")
    version, width, height = struct.unpack_from("<3i", data, 0)
    if version != 1:
        raise TmodError(f"不支持的 rawimg 版本: {version}")
    pix = data[12:12 + width * height * 4]
    if len(pix) != width * height * 4:
        raise TmodError("rawimg 像素数据长度不符")
    img = Image.frombytes("RGBA", (width, height), pix)
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def extract_tmod(
    path: str,
    out_dir: str,
    log: Optional[LogFn] = None,
    progress: Optional[ProgressFn] = None,
    verify_hash: bool = True,
    convert_rawimg: bool = False,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> int:
    """
    解包单个 .tmod 文件到 out_dir,保持原始目录结构。

    返回成功提取的文件数。抛出 TmodError 表示致命错误。

    参数:
      path          : .tmod 文件路径
      out_dir       : 输出根目录(模组将解到 out_dir/<mod_name>/ 下)
      log           : 日志回调 (level, message)
      progress      : 进度回调 (current_index, total, filename)
      verify_hash   : 是否在哈希不匹配时报错(默认仅警告,不中断)
      convert_rawimg: 是否将 .rawimg 同步转为 .png(保留原文件)
      cancel_check  : 返回 True 时尽快中止(用于 GUI 取消)
    """
    def _log(level: str, msg: str) -> None:
        if log:
            try:
                log(level, msg)
            except Exception:
                pass

    _log("INFO", f"正在解析: {os.path.basename(path)}")
    info = parse_tmod(path)
    _log("INFO", f"模组: {info.mod_name} v{info.mod_version}"
                 f"  (tModLoader {info.tmod_version}, {'旧版' if info.is_legacy else '新版'}格式)")
    _log("INFO", f"包含 {info.file_count} 个文件,"
                 f"签名: {'已签名' if info.signed else '未签名'}")

    if info.hash_verified is True:
        _log("INFO", "SHA1 校验通过,文件完整")
    elif info.hash_verified is False:
        msg = "SHA1 校验失败,文件可能已被篡改或损坏"
        if verify_hash:
            _log("WARN", msg + "(仍将尝试解包)")
        else:
            _log("WARN", msg)
    else:
        _log("WARN", "无法计算 SHA1 校验")

    # 输出目录: out_dir/<mod_name>
    dest_root = _safe_join(out_dir, info.mod_name)
    os.makedirs(dest_root, exist_ok=True)

    with open(path, "rb") as f:
        data = f.read()

    total = len(info.files)
    ok = 0

    if info.is_legacy:
        # 旧版:整体解压一次,再顺序读取各文件原始数据
        hash_start = 4 + len(info.tmod_version.encode("utf-8")) + 1 + 20 + 256 + 4
        # 注: _read_7bit_string 的长度前缀字节数需重算,这里直接用数据长度定位
        # 旧版头部 = 4(magic) + 7bitstr(version) + 20 + 256 + 4
        off = 4
        # 跳过版本字符串
        _, off = _read_7bit_string(data, off)
        off += 20 + 256 + 4  # hash + sig + datalen
        blob = zlib.decompress(data[off:], -15)
        bo = 0
        _, bo = _read_7bit_string(blob, bo)  # mod name
        _, bo = _read_7bit_string(blob, bo)  # mod version
        struct.unpack_from("<i", blob, bo)[0]  # count
        bo += 4
        for idx, entry in enumerate(info.files):
            if cancel_check and cancel_check():
                _log("WARN", "用户已取消解包")
                break
            if progress:
                progress(idx, total, entry.name)
            # 跳过文件名与长度(已在文件表),定位到数据
            fname, bo = _read_7bit_string(blob, bo)
            fsize = struct.unpack_from("<i", blob, bo)[0]
            bo += 4
            content = blob[bo:bo + fsize]
            bo += fsize
            try:
                _write_file(dest_root, fname, content, convert_rawimg, _log)
                ok += 1
            except Exception as e:
                _log("ERROR", f"写入失败 {fname}: {e}")
        if progress:
            progress(total, total, "")
    else:
        # 新版:定位文件表结束,顺序读取压缩数据
        off = 4
        _, off = _read_7bit_string(data, off)        # version
        off += 20 + 256 + 4                          # hash + sig + datalen
        _, off = _read_7bit_string(data, off)        # mod name
        _, off = _read_7bit_string(data, off)        # mod version
        off += 4                                     # count
        for entry in info.files:                     # 跳过文件表
            _, off = _read_7bit_string(data, off)
            off += 8
        for idx, entry in enumerate(info.files):
            if cancel_check and cancel_check():
                _log("WARN", "用户已取消解包")
                break
            if progress:
                progress(idx, total, entry.name)
            raw = data[off:off + entry.compressed]
            off += entry.compressed
            try:
                if entry.compressed != entry.size:
                    content = _decompress(raw, entry.size)
                else:
                    content = raw
                _write_file(dest_root, entry.name, content, convert_rawimg, _log)
                ok += 1
            except Exception as e:
                _log("ERROR", f"解包失败 {entry.name}: {e}")
        if progress:
            progress(total, total, "")

    _log("INFO", f"完成: {ok}/{total} 个文件 -> {dest_root}")
    return ok


def _write_file(dest_root: str, name: str, content: bytes,
                convert_rawimg: bool, _log) -> None:
    out_path = _safe_join(dest_root, name)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(content)
    if convert_rawimg and name.lower().endswith(".rawimg"):
        try:
            png_bytes = _rawimg_to_png(content)
            png_path = os.path.splitext(out_path)[0] + ".png"
            with open(png_path, "wb") as f:
                f.write(png_bytes)
        except Exception as e:
            _log("WARN", f"rawimg->PNG 转换失败 {name}: {e}")
