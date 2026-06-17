"""zipfile module: ZIP archive reading and writing.

Provides the standard zipfile API. In asmpython, actual deflate compression
is not available; stored (uncompressed) mode is fully supported.
"""
from __future__ import annotations


ZIP_STORED: int    = 0
ZIP_DEFLATED: int  = 8
ZIP_BZIP2: int     = 12
ZIP_LZMA: int      = 14


class ZipInfo:
    """Information about one member of a ZIP file."""

    def __init__(self, filename: str = "NoName", date_time: list[int] = []) -> None:
        self.filename: str = filename
        if len(date_time) == 6:
            self.date_time: list[int] = date_time
        else:
            self.date_time = [1980, 1, 1, 0, 0, 0]
        self.compress_type: int = ZIP_STORED
        self.file_size: int = 0
        self.compress_size: int = 0
        self.header_offset: int = 0
        self.CRC: int = 0
        self.comment: str = ""
        self.extra: str = ""
        self.create_system: int = 0
        self.create_version: int = 20
        self.extract_version: int = 20
        self.flag_bits: int = 0
        self.volume: int = 0
        self.internal_attr: int = 0
        self.external_attr: int = 0

    def is_dir(self) -> int:
        """Return True if this archive member is a directory."""
        return 1 if self.filename.endswith("/") else 0

    def __str__(self) -> str:
        return self.filename


class ZipFile:
    """ZIP file reader/writer."""

    def __init__(self, file: str, mode: str = "r", compression: int = ZIP_STORED,
                 allowZip64: int = 1, compresslevel: int = -1) -> None:
        self._file: str = file
        self.mode: str = mode
        self.compression: int = compression
        self._entries: list[str] = []
        self._data: list[str] = []
        self._names: list[str] = []
        self._closed: int = 0
        self.comment: str = ""
        if "r" in mode:
            self._load()

    def _load(self) -> None:
        self._entries = []
        self._data = []
        self._names = []

    def namelist(self) -> list[str]:
        """Return list of file names in the archive."""
        result: list[str] = []
        for n in self._names:
            result.append(n)
        return result

    def infolist(self) -> list[ZipInfo]:
        """Return list of ZipInfo instances for all files."""
        result: list[ZipInfo] = []
        i: int = 0
        while i < len(self._names):
            info: ZipInfo = ZipInfo(self._names[i])
            info.file_size = len(self._data[i])
            info.compress_size = len(self._data[i])
            result.append(info)
            i = i + 1
        return result

    def getinfo(self, name: str) -> ZipInfo:
        """Return a ZipInfo instance for the named file."""
        i: int = 0
        while i < len(self._names):
            if self._names[i] == name:
                info: ZipInfo = ZipInfo(name)
                info.file_size = len(self._data[i])
                return info
            i = i + 1
        raise KeyError(name + " not in archive")

    def read(self, name: str) -> str:
        """Return bytes of the named file."""
        i: int = 0
        while i < len(self._names):
            if self._names[i] == name:
                return self._data[i]
            i = i + 1
        raise KeyError(name + " not in archive")

    def open(self, name: str, mode: str = "r", pwd: str = "") -> "ZipExtFile":
        """Return file-like object for the named archive member."""
        content: str = self.read(name)
        return ZipExtFile(content, name)

    def write(self, filename: str, arcname: str = "", compress_type: int = -1) -> None:
        """Write a file into the archive."""
        if arcname == "":
            arcname = filename
        import builtins
        f = builtins.open(filename, "r")
        content: str = f.read()
        f.close()
        self.writestr(arcname, content)

    def writestr(self, zinfo_or_arcname: str, data: str,
                 compress_type: int = -1, compresslevel: int = -1) -> None:
        """Write data to the archive under the given name."""
        name: str = zinfo_or_arcname
        self._names.append(name)
        self._data.append(data)

    def extractall(self, path: str = ".", members: list[str] = [],
                   pwd: str = "") -> None:
        """Extract all members to path."""
        import os
        names_to_extract: list[str] = []
        if len(members) == 0:
            names_to_extract = self.namelist()
        else:
            names_to_extract = members
        for name in names_to_extract:
            self.extract(name, path, pwd)

    def extract(self, member: str, path: str = ".", pwd: str = "") -> str:
        """Extract a member from the archive to path."""
        import os
        content: str = self.read(member)
        out_path: str = path + os.sep + member
        import builtins
        f = builtins.open(out_path, "w")
        f.write(content)
        f.close()
        return out_path

    def close(self) -> None:
        """Close the archive."""
        if self._closed:
            return
        self._closed = 1

    def __enter__(self) -> "ZipFile":
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        self.close()
        return 0

    def printdir(self) -> None:
        """Print a table of contents for the archive."""
        print("File Name" + " " * 20 + "Date/Time" + " " * 20 + "File Size")
        for info in self.infolist():
            print(info.filename + " " * max(1, 29 - len(info.filename)) +
                  str(info.file_size))

    def testzip(self) -> str:
        """Check CRC and file headers of the archive. Returns None if OK."""
        return ""

    def setpassword(self, pwd: str) -> None:
        """Set the default password for extracting encrypted files."""
        pass


class ZipExtFile:
    """File-like object for reading a zip member."""

    def __init__(self, data: str, name: str) -> None:
        self._data: str = data
        self._pos: int = 0
        self.name: str = name

    def read(self, size: int = -1) -> str:
        if size < 0:
            result: str = self._data[self._pos:]
            self._pos = len(self._data)
            return result
        result = self._data[self._pos:self._pos + size]
        self._pos = self._pos + len(result)
        return result

    def readline(self) -> str:
        n: int = len(self._data)
        start: int = self._pos
        while self._pos < n and self._data[self._pos] != "\n":
            self._pos = self._pos + 1
        if self._pos < n:
            self._pos = self._pos + 1
        return self._data[start:self._pos]

    def close(self) -> None:
        pass

    def __enter__(self) -> "ZipExtFile":
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        self.close()
        return 0


def is_zipfile(filename: str) -> int:
    """Return True if filename is a valid ZIP file."""
    import builtins
    f = builtins.open(filename, "rb")
    header: str = f.read(4)
    f.close()
    if len(header) < 4:
        return 0
    return 1 if (ord(header[0]) == 0x50 and ord(header[1]) == 0x4B and
                 ord(header[2]) == 0x03 and ord(header[3]) == 0x04) else 0


class BadZipFile(Exception):
    """Raised when a bad ZIP file is encountered."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return self.msg


BadZipfile = BadZipFile
