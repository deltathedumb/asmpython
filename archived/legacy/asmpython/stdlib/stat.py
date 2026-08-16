"""stat module: constants and functions for interpreting os.stat() results.

Provides the mode-bit constants (S_IFREG, S_IFDIR, ...) and the helper
predicates (S_ISREG, S_ISDIR, ...) that CPython's stat module exposes.
All values match the POSIX / Linux definitions; Windows uses a compatible
subset.
"""
from __future__ import annotations

# File-type mask and type codes (upper 4 bits of st_mode).
S_IFMT: int = 0o170000    # bit mask for the file type bit field
S_IFSOCK: int = 0o140000  # socket
S_IFLNK: int = 0o120000   # symbolic link
S_IFREG: int = 0o100000   # regular file
S_IFBLK: int = 0o060000   # block device
S_IFDIR: int = 0o040000   # directory
S_IFCHR: int = 0o020000   # character device
S_IFIFO: int = 0o010000   # FIFO

# Permission bits.
S_ISUID: int = 0o4000     # set-user-ID bit
S_ISGID: int = 0o2000     # set-group-ID bit
S_ISVTX: int = 0o1000     # sticky bit

S_IRWXU: int = 0o700      # owner: rwx
S_IRUSR: int = 0o400      # owner: read
S_IWUSR: int = 0o200      # owner: write
S_IXUSR: int = 0o100      # owner: execute

S_IRWXG: int = 0o070      # group: rwx
S_IRGRP: int = 0o040      # group: read
S_IWGRP: int = 0o020      # group: write
S_IXGRP: int = 0o010      # group: execute

S_IRWXO: int = 0o007      # others: rwx
S_IROTH: int = 0o004      # others: read
S_IWOTH: int = 0o002      # others: write
S_IXOTH: int = 0o001      # others: execute

# Convenience combos used by open()/chmod().
S_IREAD: int = S_IRUSR
S_IWRITE: int = S_IWUSR
S_IEXEC: int = S_IXUSR

# File-type predicate functions.
def S_ISREG(mode: int) -> int:
    """Return non-zero if mode is from a regular file."""
    return 1 if (mode & S_IFMT) == S_IFREG else 0


def S_ISDIR(mode: int) -> int:
    """Return non-zero if mode is from a directory."""
    return 1 if (mode & S_IFMT) == S_IFDIR else 0


def S_ISLNK(mode: int) -> int:
    """Return non-zero if mode is from a symbolic link."""
    return 1 if (mode & S_IFMT) == S_IFLNK else 0


def S_ISSOCK(mode: int) -> int:
    """Return non-zero if mode is from a socket."""
    return 1 if (mode & S_IFMT) == S_IFSOCK else 0


def S_ISBLK(mode: int) -> int:
    """Return non-zero if mode is from a block special device."""
    return 1 if (mode & S_IFMT) == S_IFBLK else 0


def S_ISCHR(mode: int) -> int:
    """Return non-zero if mode is from a character special device."""
    return 1 if (mode & S_IFMT) == S_IFCHR else 0


def S_ISFIFO(mode: int) -> int:
    """Return non-zero if mode is from a FIFO (named pipe)."""
    return 1 if (mode & S_IFMT) == S_IFIFO else 0


def S_IMODE(mode: int) -> int:
    """Return the portion of the file's mode that can be set by os.chmod()."""
    return mode & 0o7777


def S_IFMT_func(mode: int) -> int:
    """Return the file type portion of the file's mode."""
    return mode & S_IFMT


def filemode(mode: int) -> str:
    """Convert a file's mode to a string of the form '-rwxrwxrwx'."""
    kind: int = mode & S_IFMT
    if kind == S_IFREG:
        result: str = "-"
    elif kind == S_IFDIR:
        result = "d"
    elif kind == S_IFLNK:
        result = "l"
    elif kind == S_IFBLK:
        result = "b"
    elif kind == S_IFCHR:
        result = "c"
    elif kind == S_IFIFO:
        result = "p"
    elif kind == S_IFSOCK:
        result = "s"
    else:
        result = "?"
    result = result + ("r" if mode & S_IRUSR else "-")
    result = result + ("w" if mode & S_IWUSR else "-")
    if mode & S_ISUID:
        result = result + ("s" if mode & S_IXUSR else "S")
    else:
        result = result + ("x" if mode & S_IXUSR else "-")
    result = result + ("r" if mode & S_IRGRP else "-")
    result = result + ("w" if mode & S_IWGRP else "-")
    if mode & S_ISGID:
        result = result + ("s" if mode & S_IXGRP else "S")
    else:
        result = result + ("x" if mode & S_IXGRP else "-")
    result = result + ("r" if mode & S_IROTH else "-")
    result = result + ("w" if mode & S_IWOTH else "-")
    if mode & S_ISVTX:
        result = result + ("t" if mode & S_IXOTH else "T")
    else:
        result = result + ("x" if mode & S_IXOTH else "-")
    return result


# Indices into the st_* fields returned by os.stat() (tuple layout).
ST_MODE: int = 0
ST_INO: int = 1
ST_DEV: int = 2
ST_NLINK: int = 3
ST_UID: int = 4
ST_GID: int = 5
ST_SIZE: int = 6
ST_ATIME: int = 7
ST_MTIME: int = 8
ST_CTIME: int = 9

# UF_* and SF_* flags (BSD/macOS; stubs on Linux/Windows).
UF_NODUMP: int = 0x00000001
UF_IMMUTABLE: int = 0x00000002
UF_APPEND: int = 0x00000004
UF_OPAQUE: int = 0x00000008
UF_NOUNLINK: int = 0x00000010
UF_COMPRESSED: int = 0x00000020
UF_HIDDEN: int = 0x00008000
SF_ARCHIVED: int = 0x00010000
SF_IMMUTABLE: int = 0x00020000
SF_APPEND: int = 0x00040000
SF_NOUNLINK: int = 0x00100000
SF_SNAPSHOT: int = 0x00200000
