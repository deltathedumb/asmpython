# COVERAGE: PurePosixPath's parsing (parts, name, stem, suffix, suffixes,
# parent, parents, anchor, root, is_absolute), its algebra (`/`, joinpath,
# with_name, with_suffix, with_stem, relative_to, match, as_posix), equality
# and ordering, `__fspath__`; and the CONCRETE half -- exists, is_file,
# is_dir, read_bytes, write_bytes, read_text, write_text, mkdir, touch,
# unlink, rmdir, and the errors each raises. NOT covered: iterdir, glob,
# rglob, stat, resolve, absolute and open, which this module refuses by name
# rather than answering wrongly (docs/STDLIB.md), and which therefore cannot
# appear in a program whose output must equal CPython's.
#
# THE FILES ARE MADE AND REMOVED BY THIS PROGRAM. It runs in the repository
# root under both interpreters, so every name here is unique enough not to
# collide with a real one and every path is removed before the program ends --
# a leftover file makes the SECOND run of this test disagree with the first,
# which looks exactly like a miscompile.
#
# WHAT THE NEWLINES ARE DOING HERE. `write_text` is text mode and text mode
# translates: on Windows a `\n` handed to it reaches the disk as `\r\n`, and
# `read_text` turns it back. That is CPython's behaviour, so it has to be this
# module's too -- and the only way to SEE it is to write with `write_text` and
# read back with `read_bytes`, which is why that pairing is here twice.
import pathlib
from pathlib import PurePosixPath

# A RUN THAT DIED HALFWAY LEFT ITS FILES BEHIND, and the next run then differs
# from CPython at whichever line first trips over one -- a failure that reads
# as a miscompile and is not one. Both interpreters clear the same three names
# before starting, so a run is the same whatever the one before it did. It
# cannot be a loop over the directory: this module refuses `iterdir`.
for _stale in ("apy-pathlib-case-dir/inside.txt", "apy-pathlib-case.txt",
               "apy-pathlib-nothing-here.txt"):
    pathlib.Path(_stale).unlink(missing_ok=True)
if pathlib.Path("apy-pathlib-case-dir").is_dir():
    pathlib.Path("apy-pathlib-case-dir").rmdir()

# ---- parsing ---------------------------------------------------------------
p = PurePosixPath("/a/b/c.txt")
print(p.parts, p.name, p.stem, p.suffix, p.suffixes)
print(str(p.parent), [str(one) for one in p.parents])
print(p.anchor, p.root, p.is_absolute())

rel = PurePosixPath("a/b/c.tar.gz")
print(rel.parts, rel.name, rel.stem, rel.suffix, rel.suffixes)
print(str(rel.parent), rel.anchor, rel.root, rel.is_absolute())

# A trailing slash, a doubled one and a `.` are all NOISE in a path and the
# parser drops them; `..` is not, because removing it needs to know what the
# directory before it really is.
print(PurePosixPath("a//b/./c/").parts)
print(PurePosixPath("a/../b").parts)
print(PurePosixPath("").parts, str(PurePosixPath("")))
print(PurePosixPath(".").parts, str(PurePosixPath(".")))
print(PurePosixPath("/").parts, str(PurePosixPath("/")))

# A name with no stem, and a dotfile -- `.bashrc` is a NAME and not a suffix,
# which is the one case the obvious rsplit gets wrong.
print(PurePosixPath("/a/.bashrc").name, repr(PurePosixPath("/a/.bashrc").suffix))
print(repr(PurePosixPath("/a/x.").suffix), PurePosixPath("/a/x.").stem)

# ---- the algebra -----------------------------------------------------------
print(str(PurePosixPath("a") / "b" / "c"))
print(str(PurePosixPath("a").joinpath("b", "c")))
print(str(PurePosixPath("a/b") / PurePosixPath("c")))
# An ABSOLUTE right-hand side replaces the left rather than extending it.
print(str(PurePosixPath("a/b") / "/x"))

print(str(p.with_name("d.md")), str(p.with_suffix(".md")))
print(str(p.with_suffix("")), str(p.with_stem("z")))
print(str(rel.relative_to("a")), str(rel.relative_to("a/b")))
print(p.as_posix(), rel.as_posix())
print(p.match("*.txt"), p.match("b/*.txt"), p.match("/a/*/*.txt"))

# ---- protocol ---------------------------------------------------------------
print(PurePosixPath("a/b") == PurePosixPath("a/b"))
print(PurePosixPath("a/b") == PurePosixPath("a/c"))
print(PurePosixPath("a/b") < PurePosixPath("a/c"))
print(p.__fspath__())
print(repr(PurePosixPath("a/b")))

# ---- the concrete half -----------------------------------------------------
f = pathlib.Path("apy-pathlib-case.txt")
print(f.exists(), f.is_file(), f.is_dir())

f.write_bytes(b"one\ntwo")
print(f.exists(), f.is_file(), f.is_dir())
print(f.read_bytes(), repr(f.read_text()))

# BYTES ARE NOT TRANSLATED. `write_bytes` put a bare `\n` on the disk and
# `read_bytes` gives it back; `read_text` is the one that translates, and
# there is nothing here for it to translate.
f.write_text("one\ntwo")
print(f.read_bytes(), repr(f.read_text()))

# Content with no newline at all, so the two paths agree on a file whose
# length the translation cannot change.
f.write_text("plain")
print(f.read_bytes(), repr(f.read_text()), len(f.read_text()))

# An EMPTY file: zero bytes is the length the read loop is most likely to get
# wrong, because there is nothing to read and the answer is still not an error.
f.write_bytes(b"")
print(f.read_bytes(), repr(f.read_text()), f.exists(), f.is_file())

# A NUL and a high byte, which is what distinguishes a byte count from a C
# string's length and a decode from a copy.
f.write_bytes(b"a\x00b\xff")
print(f.read_bytes(), len(f.read_bytes()))

f.unlink()
print(f.exists(), f.is_file())

# `touch` makes an empty file where there is none and leaves one that exists.
f.touch()
print(f.exists(), f.read_bytes())
f.write_bytes(b"kept")
f.touch()
print(f.read_bytes())
f.unlink()

# ---- directories -----------------------------------------------------------
d = pathlib.Path("apy-pathlib-case-dir")
print(d.exists(), d.is_dir(), d.is_file())
d.mkdir()
print(d.exists(), d.is_dir(), d.is_file())

# `as_posix` AND NOT `str`, because the separator is the one thing these two
# disagree about on purpose: CPython's `Path` on Windows is a `WindowsPath` and
# renders `\`, this module's is POSIX-flavoured and renders `/`. `as_posix` is
# what both spell the same way, so this line tests that `/` built the right
# path rather than re-testing the divergence the module docstring declares.
inner = d / "inside.txt"
print(inner.as_posix(), inner.exists())
inner.write_text("nested")
print(inner.exists(), inner.is_file(), repr(inner.read_text()))
inner.unlink()

d.rmdir()
print(d.exists(), d.is_dir())

# ---- the errors ------------------------------------------------------------
missing = pathlib.Path("apy-pathlib-nothing-here.txt")
print(missing.exists(), missing.is_file(), missing.is_dir())
try:
    missing.read_bytes()
except FileNotFoundError:
    print("read_bytes: FileNotFoundError")
try:
    missing.read_text()
except FileNotFoundError:
    print("read_text: FileNotFoundError")
try:
    missing.unlink()
except FileNotFoundError:
    print("unlink: FileNotFoundError")
print(missing.unlink(missing_ok=True))

# `mkdir` over something that is already there raises unless told not to.
d.mkdir()
try:
    d.mkdir()
except FileExistsError:
    print("mkdir: FileExistsError")
print(d.mkdir(exist_ok=True))
d.rmdir()
print("done", d.exists(), missing.exists())
