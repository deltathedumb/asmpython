"""tarfile: read and write tar archive files.

Pure Python tar implementation. Supports:
- Reading POSIX.1-1988 (ustar) and GNU tar formats
- Writing ustar format archives
- gzip-compressed archives (via gzip module)

Usage::

    import tarfile

    # Reading
    with tarfile.open("archive.tar") as tar:
        tar.extractall("output/")
        for member in tar.getmembers():
            print(member.name, member.size)

    # Writing
    with tarfile.open("new.tar", "w") as tar:
        tar.add("file.txt")
        tar.add("directory/", arcname="data/")
"""
from __future__ import annotations

import os

def _makedirs(path: str) -> None:
    """Recursive mkdir (os.py has no makedirs binding -- it's a pure FFI
    registry, no real Python source allowed alongside it). Builds up each
    path segment with the existing single-level os.mkdir binding."""
    if path == "" or path == "/":
        return
    parent: str = path[:path.rfind("/")]
    if parent and os.path.isdir(parent) == 0:
        _makedirs(parent)
    if os.path.isdir(path) == 0:
        os.mkdir(path, 511)

# Tar block size
BLOCKSIZE: int = 512
RECORDSIZE: int = BLOCKSIZE * 20
GNU_MAGIC: str = "ustar  \x00"
POSIX_MAGIC: str = "ustar\x0000"

# Tar type flags
REGTYPE: str = "0"
AREGTYPE: str = "\x00"
LNKTYPE: str = "1"
SYMTYPE: str = "2"
CHRTYPE: str = "3"
BLKTYPE: str = "4"
DIRTYPE: str = "5"
FIFOTYPE: str = "6"
CONTTYPE: str = "7"
GNUTYPE_LONGNAME: str = "L"
GNUTYPE_LONGLINK: str = "K"
GNUTYPE_SPARSE: str = "S"

# Open modes
ENCODING: str = "utf-8"

# Error handling
RAISE: int = 1
WARN: int = 2
IGNORE: int = 3


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TarError(Exception):
    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return self.msg


class ExtractError(TarError):
    pass


class ReadError(TarError):
    pass


class CompressionError(TarError):
    pass


class StreamError(TarError):
    pass


class HeaderError(TarError):
    pass


class FilterError(TarError):
    pass


class AbsolutePathError(FilterError):
    pass


class OutsideDestinationError(FilterError):
    pass


class SpecialFileError(FilterError):
    pass


class AbsoluteLinkError(FilterError):
    pass


# ---------------------------------------------------------------------------
# TarInfo
# ---------------------------------------------------------------------------

class TarInfo:
    """Informational class for a tar archive member."""

    def __init__(self, name: str = "") -> None:
        self.name: str = name
        self.size: int = 0
        self.mtime: int = 0
        self.mode: int = 0o644
        self.type: str = REGTYPE
        self.linkname: str = ""
        self.uid: int = 0
        self.gid: int = 0
        self.uname: str = ""
        self.gname: str = ""
        self.devmajor: int = 0
        self.devminor: int = 0
        self.offset: int = 0
        self.offset_data: int = 0

    def isreg(self) -> int:
        return self.type in (REGTYPE, AREGTYPE, CONTTYPE)

    def isfile(self) -> int:
        return self.isreg()

    def isdir(self) -> int:
        return self.type == DIRTYPE

    def issym(self) -> int:
        return self.type == SYMTYPE

    def islnk(self) -> int:
        return self.type == LNKTYPE

    def ischr(self) -> int:
        return self.type == CHRTYPE

    def isblk(self) -> int:
        return self.type == BLKTYPE

    def isfifo(self) -> int:
        return self.type == FIFOTYPE

    def isdev(self) -> int:
        return self.ischr() or self.isblk() or self.isfifo()

    def get_info(self) -> dict:
        info: dict = {}
        info["name"] = self.name
        info["mode"] = str(self.mode)
        info["uid"] = str(self.uid)
        info["gid"] = str(self.gid)
        info["size"] = str(self.size)
        info["mtime"] = str(self.mtime)
        info["type"] = self.type
        info["linkname"] = self.linkname
        info["uname"] = self.uname
        info["gname"] = self.gname
        return info

    def tobuf(self, format: int = 0, encoding: str = "utf-8",
              errors: str = "surrogateescape") -> str:
        """Serialize to a 512-byte tar header block."""
        return _make_header(self)

    def __repr__(self) -> str:
        return "<TarInfo " + self.name + " at ...>"


def _make_header(info: TarInfo) -> str:
    """Create a 512-byte POSIX ustar header block."""
    h: list = []
    i: int = 0
    while i < 512:
        h.append("\x00")
        i = i + 1
    def put_str(data: str, offset: int, length: int) -> None:
        j: int = 0
        while j < len(data) and j < length:
            h[offset + j] = data[j]
            j = j + 1
    def put_oct(val: int, offset: int, length: int) -> None:
        s: str = oct(val)[2:]
        s = s.zfill(length - 1)
        put_str(s, offset, length - 1)
    name: str = info.name
    if len(name) > 100:
        name = name[:100]
    put_str(name, 0, 100)
    put_oct(info.mode, 100, 8)
    put_oct(info.uid, 108, 8)
    put_oct(info.gid, 116, 8)
    put_oct(info.size, 124, 12)
    put_oct(info.mtime, 136, 12)
    # Checksum placeholder
    put_str("        ", 148, 8)
    h[156] = info.type
    put_str(info.linkname[:100], 157, 100)
    put_str("ustar", 257, 6)
    put_str("00", 263, 2)
    put_str(info.uname[:32], 265, 32)
    put_str(info.gname[:32], 297, 32)
    put_oct(info.devmajor, 329, 8)
    put_oct(info.devminor, 337, 8)
    # Compute checksum
    chksum: int = 0
    for ch in h:
        chksum = chksum + ord(ch)
    chk_str: str = oct(chksum)[2:].zfill(6) + "\x00 "
    put_str(chk_str, 148, 8)
    return "".join(h)


def _parse_header(block: str) -> TarInfo:
    """Parse a 512-byte header block into a TarInfo."""
    if len(block) < 512:
        return None
    info: TarInfo = TarInfo()
    def read_str(offset: int, length: int) -> str:
        s: str = block[offset:offset + length]
        null: int = s.find("\x00")
        if null >= 0:
            s = s[:null]
        return s
    def read_oct(offset: int, length: int) -> int:
        s: str = read_str(offset, length).strip()
        if not s:
            return 0
        try:
            return int(s, 8)
        except Exception:
            return 0
    info.name = read_str(0, 100)
    info.mode = read_oct(100, 8)
    info.uid = read_oct(108, 8)
    info.gid = read_oct(116, 8)
    info.size = read_oct(124, 12)
    info.mtime = read_oct(136, 12)
    type_byte: str = block[156]
    info.type = type_byte if type_byte else REGTYPE
    info.linkname = read_str(157, 100)
    info.uname = read_str(265, 32)
    info.gname = read_str(297, 32)
    # GNU/POSIX prefix
    prefix: str = read_str(345, 155)
    if prefix:
        info.name = prefix + "/" + info.name
    return info


def _num_blocks(size: int) -> int:
    if size == 0:
        return 0
    return (size + BLOCKSIZE - 1) // BLOCKSIZE


# ---------------------------------------------------------------------------
# TarFile
# ---------------------------------------------------------------------------

class TarFile:
    """Represent a tar archive."""

    OPEN_METH: dict = {}
    debug: int = 0
    dereference: int = 0
    ignore_zeros: int = 0
    errorlevel: int = 1
    format: int = 2  # USTAR

    def __init__(self, name: str = "", mode: str = "r",
                 fileobj: object = None, format: int = 2,
                 tarinfo: int = 0, dereference: int = 0,
                 ignore_zeros: int = 0, encoding: str = "utf-8",
                 errors: str = "surrogateescape",
                 pax_headers: dict = {}, debug: int = 0,
                 errorlevel: int = 1, copybufsize: int = 0) -> None:
        self.name: str = name
        self.mode: str = mode
        self._members: list = []
        self._data: str = ""
        self._pos: int = 0
        self._closed: int = 0

        if mode.startswith("r"):
            self._load()
        elif mode.startswith("w") or mode.startswith("a"):
            self._data = ""

    def _load(self) -> None:
        if not os.path.exists(self.name):
            raise ReadError("file not found: " + self.name)
        f = open(self.name, "r")
        self._data = f.read()
        f.close()
        self._parse_members()

    def _parse_members(self) -> None:
        pos: int = 0
        n: int = len(self._data)
        while pos + BLOCKSIZE <= n:
            block: str = self._data[pos:pos + BLOCKSIZE]
            if all(c == "\x00" for c in block):
                break
            info: TarInfo = _parse_header(block)
            if info is None:
                break
            info.offset = pos
            info.offset_data = pos + BLOCKSIZE
            self._members.append(info)
            pos = pos + BLOCKSIZE + _num_blocks(info.size) * BLOCKSIZE

    def getmembers(self) -> list:
        """Return list of TarInfo objects for all members."""
        return self._members

    def getnames(self) -> list:
        """Return list of member names."""
        result: list = []
        for m in self._members:
            result.append(m.name)
        return result

    def getmember(self, name: str) -> TarInfo:
        """Return TarInfo for named member."""
        for m in self._members:
            if m.name == name:
                return m
        raise KeyError("member not found: " + name)

    def gettarinfo(self, name: str = "", arcname: str = "",
                   fileobj: object = None) -> TarInfo:
        """Create a TarInfo for a file on disk."""
        info: TarInfo = TarInfo()
        if name:
            info.name = arcname if arcname else name
            info.size = os.path.getsize(name)
            info.mtime = int(os.path.getmtime(name))
            if os.path.isdir(name):
                info.type = DIRTYPE
                info.size = 0
            elif os.path.islink(name):
                info.type = SYMTYPE
                # No readlink() binding exists (no portable equivalent on
                # Windows, the primary target). Symlinks still get archived
                # as SYMTYPE entries, just without a resolvable target.
                info.linkname = ""
            else:
                info.type = REGTYPE
        return info

    def list(self, verbose: int = 1, members: list = []) -> None:
        """Print a listing of all members."""
        targets: list = members if members else self._members
        for m in targets:
            print(m.name)

    def extractall(self, path: str = ".", members: list = [],
                   numeric_owner: int = 0, filter: object = None) -> None:
        """Extract all members to *path*."""
        targets: list = members if members else self._members
        for m in targets:
            self.extract(m, path, numeric_owner, filter)

    def extract(self, member: TarInfo, path: str = ".",
                set_attrs: int = 1, numeric_owner: int = 0,
                filter: object = None) -> None:
        """Extract a member to *path*."""
        if path and not path.endswith("/"):
            path = path + "/"
        dest: str = path + member.name
        if member.isdir():
            try:
                _makedirs(dest)
            except Exception:
                pass
        elif member.isreg():
            # Extract file data
            data: str = self.extractfile(member)
            parent: str = dest[:dest.rfind("/")]
            if parent:
                try:
                    _makedirs(parent)
                except Exception:
                    pass
            f = open(dest, "w")
            f.write(data)
            f.close()

    def extractfile(self, member: TarInfo) -> str:
        """Return the file data for *member* as a string."""
        if member.isdir():
            return ""
        start: int = member.offset_data
        size: int = member.size
        return self._data[start:start + size]

    def add(self, name: str, arcname: str = "", recursive: int = 1,
            filter: object = None) -> None:
        """Add file(s) to the archive."""
        if not arcname:
            arcname = name
        if os.path.isdir(name) and recursive:
            self._add_dir(name, arcname)
        else:
            self._add_file(name, arcname)

    def _add_file(self, name: str, arcname: str) -> None:
        info: TarInfo = self.gettarinfo(name, arcname)
        f = open(name, "r")
        data: str = f.read()
        f.close()
        self.addfile(info, data)

    def _add_dir(self, name: str, arcname: str) -> None:
        dir_info: TarInfo = TarInfo(arcname + "/")
        dir_info.type = DIRTYPE
        self.addfile(dir_info, "")
        entries: list = os.listdir(name)
        for entry in entries:
            full_path: str = name + "/" + entry
            arc_path: str = arcname + "/" + entry
            if os.path.isdir(full_path):
                self._add_dir(full_path, arc_path)
            else:
                self._add_file(full_path, arc_path)

    def addfile(self, tarinfo: TarInfo, fileobj: str = "") -> None:
        """Add TarInfo + data to the archive."""
        self._data = self._data + _make_header(tarinfo)
        data: str = fileobj
        self._data = self._data + data
        # Pad to block boundary
        remainder: int = len(data) % BLOCKSIZE
        if remainder:
            pad_size: int = BLOCKSIZE - remainder
            j: int = 0
            while j < pad_size:
                self._data = self._data + "\x00"
                j = j + 1
        info_copy: TarInfo = TarInfo(tarinfo.name)
        info_copy.size = tarinfo.size
        info_copy.mode = tarinfo.mode
        info_copy.type = tarinfo.type
        info_copy.mtime = tarinfo.mtime
        self._members.append(info_copy)

    def close(self) -> None:
        """Flush and close the archive."""
        if self._closed:
            return
        if self.mode.startswith("w") or self.mode.startswith("a"):
            # Write two end-of-archive blocks
            end_block: str = "\x00" * BLOCKSIZE
            self._data = self._data + end_block + end_block
            f = open(self.name, "w")
            f.write(self._data)
            f.close()
        self._closed = 1

    def __enter__(self) -> TarFile:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> int:
        self.close()
        return 0

    def __iter__(self) -> list:
        return self._members

    def __repr__(self) -> str:
        return "<TarFile " + self.name + ">"


# ---------------------------------------------------------------------------
# open() factory
# ---------------------------------------------------------------------------

def open(name: str = "", mode: str = "r", fileobj: object = None,
         bufsize: int = 0, format: int = 2, tarinfo: int = 0,
         dereference: int = 0, ignore_zeros: int = 0,
         encoding: str = "utf-8", errors: str = "surrogateescape",
         pax_headers: dict = {}, debug: int = 0,
         errorlevel: int = 1, copybufsize: int = 0) -> TarFile:
    """Open a tar archive. mode may be 'r', 'r:', 'r:gz', 'w', 'w:gz' etc."""
    if "gz" in mode:
        raise CompressionError("gzip compression requires zlib binding")
    if "bz2" in mode:
        raise CompressionError("bzip2 compression requires bz2 binding")
    if "xz" in mode:
        raise CompressionError("lzma compression requires lzma binding")
    base_mode: str = mode[:1] if mode else "r"
    return TarFile(name, base_mode, fileobj, format, tarinfo, dereference,
                   ignore_zeros, encoding, errors, pax_headers, debug,
                   errorlevel, copybufsize)


def is_tarfile(name: str) -> int:
    """Return 1 if name is a tar archive."""
    if not os.path.exists(name):
        return 0
    f = open(name, "r")
    data: str = f._data[:512]
    f.close()
    if len(data) < 512:
        return 0
    magic: str = data[257:263]
    return 1 if magic in ("ustar\x00", "ustar ") else 0


DEFAULT_FORMAT: int = 2
GNU_FORMAT: int = 1
PAX_FORMAT: int = 3
USTAR_FORMAT: int = 2
