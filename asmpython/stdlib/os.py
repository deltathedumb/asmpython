"""os module: a small handful of POSIX/CRT primitives.

Cross-platform basics only. Avoid anything that would require structs or
opaque handles (file descriptors are fine since they're ints).
"""
from __future__ import annotations

from . import Const, Func


BINDINGS = {
    # Returns the child process's exit status.
    "system": Func(arg_types=("str",), ret_type="int", c_name="system"),
    # getenv(name) — returns a pointer; we treat NULL as the empty string at
    # the moment (no nullability tracking yet).
    "getenv": Func(arg_types=("str",), ret_type="str", c_name="getenv"),
    # exit(code)
    "_exit":  Func(arg_types=("int",), ret_type="int", c_name="exit"),

    # --- File I/O (C stdio) --------------------------------------------------
    # These bind directly to libc/msvcrt stdio. A FILE* is an opaque pointer,
    # which is just an 8-byte int slot in asmpython; 0 means NULL (failure).
    # Char-at-a-time fgetc avoids needing a caller-allocated buffer, so a whole
    # file can be read with pure FFI by appending each char to a string.
    #
    # fopen(path, mode) -> FILE* handle. Typed `str` because the result is a
    # full 64-bit pointer (not a 32-bit C int): an int return would be sign-
    # extended from EAX and truncate the pointer. A NULL (0) handle = failure.
    "fopen":  Func(arg_types=("str", "str"), ret_type="str", c_name="fopen"),
    # fgetc(FILE*) -> next byte (0..255), or -1 (EOF). The handle is a pointer,
    # passed as `str`; the return is a real C int.
    "fgetc":  Func(arg_types=("str",), ret_type="int", c_name="fgetc"),
    # fputc(char, FILE*) -> char written, or EOF on error.
    "fputc":  Func(arg_types=("int", "str"), ret_type="int", c_name="fputc"),
    # fputs(str, FILE*) -> non-negative on success, EOF on error.
    "fputs":  Func(arg_types=("str", "str"), ret_type="int", c_name="fputs"),
    # fclose(FILE*) -> 0 on success.
    "fclose": Func(arg_types=("str",), ret_type="int", c_name="fclose"),
    # fflush(FILE*) -> 0 on success (flush buffered output).
    "fflush": Func(arg_types=("str",), ret_type="int", c_name="fflush"),
    # feof(FILE*) -> non-zero once end-of-file has been hit.
    "feof":   Func(arg_types=("str",), ret_type="int", c_name="feof"),
    # File positioning: ftell -> current offset; fseek(FILE*, off, whence) -> 0
    # on success (whence: 0=SET, 1=CUR, 2=END); rewind resets to the start.
    "ftell":  Func(arg_types=("str",), ret_type="int", c_name="ftell"),
    "fseek":  Func(arg_types=("str", "int", "int"), ret_type="int", c_name="fseek"),
    "rewind": Func(arg_types=("str",), ret_type="int", c_name="rewind"),
    # rename(old, new) -> 0 on success (ISO C stdio; same name both platforms).
    "rename": Func(arg_types=("str", "str"), ret_type="int", c_name="rename"),
    # access(path, mode) -> 0 if the path is accessible for `mode`
    # (mode 0 = existence). On Windows the CRT spells it `_access`; the
    # `asmpython.stdlib.ospath` helpers hide that difference.
    "_access": Func(arg_types=("str", "int"), ret_type="int", c_name="access", c_name_windows="_access"),

    # --- filesystem mutation / queries (used by pathlib.Path) ---------------
    # mkdir(path, mode) -> 0 on success. POSIX mkdir takes (path, mode);
    # Windows' `_mkdir` only takes (path) and ignores the extra mode arg
    # (it just sits unused in the register).
    "mkdir":  Func(arg_types=("str", "int"), ret_type="int", c_name="mkdir", c_name_windows="_mkdir"),
    # remove(path) -> 0 on success. Same name on both platforms (ISO C stdio).
    "remove": Func(arg_types=("str",), ret_type="int", c_name="remove"),
    # rmdir(path) -> 0 on success.
    "rmdir":  Func(arg_types=("str",), ret_type="int", c_name="rmdir", c_name_windows="_rmdir"),
    # opendir/closedir: used to test whether a path is a directory (opendir
    # succeeds -> non-NULL DIR*) without needing a `stat` struct. MinGW-w64
    # provides a dirent.h compat layer, so `opendir`/`closedir` resolve on
    # Windows too.
    "_opendir":  Func(arg_types=("str",), ret_type="str", c_name="opendir"),
    "_closedir": Func(arg_types=("str",), ret_type="int", c_name="closedir"),
    # getcwd(buf, size) -> buf on success, NULL on failure.
    "_getcwd": Func(arg_types=("str", "int"), ret_type="str", c_name="getcwd", c_name_windows="_getcwd"),
    # putenv("NAME=VALUE") -> 0 on success. Modifies the current process's
    # environment in place, inherited by any later os.system() child. MSVC's
    # CRT requires the underscore-prefixed name; POSIX libc uses the bare one.
    "_putenv": Func(arg_types=("str",), ret_type="int", c_name="putenv", c_name_windows="_putenv"),

    # stat(path, buf) -> 0 on success, writing a raw `struct stat` (Linux) /
    # `struct _stat64` (Windows) into the caller-provided buffer. `buf` is a
    # `list[int]` whose underlying data buffer (8-byte slots) is passed as
    # the raw pointer ("list_buf" -- see codegen._gen_ffi_call): a string
    # buffer can't survive the struct's embedded NUL bytes (later indexing
    # relies on strlen), but a list's int64 slots line up with the struct's
    # 8-byte fields and can be read back directly.
    "_stat": Func(arg_types=("str", "list_buf"), ret_type="int", c_name="stat", c_name_windows="_stat64"),
    # Index (in 8-byte words) of the `st_mtime` field within the `_ST_BUF_WORDS`
    # words written by `_stat`.
    # Linux: glibc x86-64 `struct stat` (144 bytes / 18 words, st_mtim.tv_sec
    # at byte offset 88 = word 11).
    # Windows: MinGW `struct _stat64` (56 bytes / 7 words, st_mtime at byte
    # offset 40 = word 5) -- verified via `offsetof`/`sizeof` against the
    # bundled MinGW toolchain's headers.
    "_ST_MTIME_WORD": Const(ty="int", value=11, value_windows=5),
    "_ST_BUF_WORDS": Const(ty="int", value=18, value_windows=7),
    # Index (in 8-byte words) of the word containing the `st_mode` field.
    # Linux: glibc `struct stat` has `st_mode` (4 bytes) at byte offset 24 ->
    # word 3 (the low 32 bits of that word).
    # Windows: MinGW `struct _stat64` has `st_mode` (2 bytes, unsigned short)
    # at byte offset 6 -> the high 16 bits of word 0 -- verified via
    # `offsetof`/`sizeof` against the bundled MinGW toolchain's headers.
    "_ST_MODE_WORD": Const(ty="int", value=3, value_windows=0),

    # st_mode file-type bitmask and the two type bits `ospath.isdir`/`isfile`
    # care about. Values are the POSIX-standard bit patterns, shared by
    # glibc and MSVCRT/MinGW alike.
    "S_IFMT":  Const(ty="int", value=0xF000),
    "S_IFDIR": Const(ty="int", value=0x4000),
    "S_IFREG": Const(ty="int", value=0x8000),

    # popen(cmd, mode) -> FILE* (pipe), like fopen; mode is "r" or "w".
    "_popen": Func(arg_types=("str", "str"), ret_type="str", c_name="popen", c_name_windows="_popen"),
    # pclose(FILE*) -> the child's exit status (shifted on POSIX; raw exit
    # code on Windows), or -1 on error.
    "_pclose": Func(arg_types=("str",), ret_type="int", c_name="pclose", c_name_windows="_pclose"),

    # --- Platform constants ---------------------------------------------------
    "sep":      Const(ty="str", value="/", value_windows="\\"),
    "linesep":  Const(ty="str", value="\n", value_windows="\r\n"),
    "curdir":   Const(ty="str", value="."),
    "pardir":   Const(ty="str", value=".."),
    "extsep":   Const(ty="str", value="."),
    "pathsep":  Const(ty="str", value=":", value_windows=";"),
    "devnull":  Const(ty="str", value="/dev/null", value_windows="nul"),
}
