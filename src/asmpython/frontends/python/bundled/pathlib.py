"""Filesystem paths as objects.

Only the PURE half: a pure path is a string with structure and never touches
a disk, so all of it is text manipulation and every answer is exact. The
concrete half -- `Path.read_text`, `exists`, `iterdir` -- needs a filesystem,
and a stub of it would be a wrong answer rather than a missing feature.

POSIX SEMANTICS, spelled out: `/` separates, a leading `/` means absolute, `.`
segments are dropped and `..` is KEPT -- resolving it needs to know what the
names refer to, which a pure path deliberately does not.
"""


class PurePosixPath:
    def __init__(self, *segments):
        text = ""
        for segment in segments:
            piece = str(segment)
            if piece.startswith("/") or not text:
                # AN ABSOLUTE SEGMENT RESTARTS THE PATH. `PurePath("/a") /
                # "/b"` is `/b`, which is what joining an absolute path means.
                text = piece
            else:
                text = text.rstrip("/") + "/" + piece
        self._raw = text if text else "."
        self._absolute = self._raw.startswith("/")
        parts = []
        for piece in self._raw.split("/"):
            if piece == "" or piece == ".":
                continue
            parts.append(piece)
        self._names = parts

    @property
    def parts(self):
        # THE ROOT IS A PART. `PurePosixPath("/a").parts` is `('/', 'a')` --
        # the leading slash is a component, not punctuation between them.
        if self._absolute:
            return tuple(["/"] + self._names)
        return tuple(self._names)

    @property
    def name(self):
        return self._names[-1] if self._names else ""

    @property
    def suffix(self):
        base = self.name
        at = base.rfind(".")
        # A LEADING DOT IS NOT A SUFFIX: `.bashrc` has none, which is why the
        # dot must be past the first character.
        return base[at:] if at > 0 else ""

    @property
    def suffixes(self):
        base = self.name
        if base.startswith("."):
            base = base[1:]
        out = []
        for piece in base.split(".")[1:]:
            out.append("." + piece)
        return out

    @property
    def stem(self):
        base = self.name
        got = self.suffix
        return base[:len(base) - len(got)] if got else base

    @property
    def parent(self):
        if not self._names:
            return self
        if len(self._names) == 1:
            return PurePosixPath("/" if self._absolute else ".")
        head = "/".join(self._names[:-1])
        return PurePosixPath(("/" + head) if self._absolute else head)

    @property
    def parents(self):
        out = []
        here = self
        while True:
            up = here.parent
            if str(up) == str(here):
                break
            out.append(up)
            here = up
        return tuple(out)

    @property
    def root(self):
        return "/" if self._absolute else ""

    @property
    def anchor(self):
        return self.root

    def is_absolute(self):
        return self._absolute

    def joinpath(self, *others):
        return PurePosixPath(str(self), *others)

    def with_name(self, name):
        if not self._names:
            raise ValueError(repr(str(self)) + " has an empty name")
        return self.parent / name

    def with_suffix(self, suffix):
        return self.with_name(self.stem + suffix)

    def with_stem(self, stem):
        return self.with_name(stem + self.suffix)

    def relative_to(self, other):
        base = other if isinstance(other, PurePosixPath) \
            else PurePosixPath(other)
        mine, theirs = self.parts, base.parts
        if mine[:len(theirs)] != theirs:
            raise ValueError(repr(str(self)) + " is not in the subpath of "
                             + repr(str(base)))
        return PurePosixPath(*mine[len(theirs):]) if mine[len(theirs):] \
            else PurePosixPath(".")

    def as_posix(self):
        return str(self)

    def __truediv__(self, other):
        return PurePosixPath(str(self), str(other))

    def __rtruediv__(self, other):
        return PurePosixPath(str(other), str(self))

    def __str__(self):
        if self._absolute:
            return "/" + "/".join(self._names)
        return "/".join(self._names) if self._names else "."

    def __repr__(self):
        return "PurePosixPath(" + repr(str(self)) + ")"

    def __eq__(self, other):
        if isinstance(other, PurePosixPath):
            return str(self) == str(other)
        return NotImplemented

    def __ne__(self, other):
        got = self.__eq__(other)
        return got if got is NotImplemented else not got

    def __hash__(self):
        return hash(str(self))

    def __lt__(self, other):
        return self.parts < other.parts

    def __fspath__(self):
        return str(self)


class PurePath(PurePosixPath):
    """The system's flavour. POSIX here -- see the module docstring."""


class PureWindowsPath(PurePosixPath):
    """Present so an import of it resolves; the separator handling a real one
    needs is not written, so it behaves as a POSIX path."""
