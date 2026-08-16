"""Which class-file version to write.

Two options decide it, and they are not equal partners:

    --java-version 21       the release the class has to load on; resolved
                            through the table below to the highest class-file
                            version a Java 21 JVM accepts, which is 65
    --class-version 75      the major version to write in the header, verbatim

`--class-version` WINS WHEN BOTH ARE GIVEN. That ordering is the reason both
exist: `--java-version` is what you reach for when you know which JVM has to
load the result and would rather not know the format number, and
`--class-version` is the escape hatch for when you know the number and do not
want it interpreted. An option that is documented as an escape hatch and then
loses to the friendlier one is not an escape hatch.

Neither is silently adjusted. A JVM refuses a class numbered above its own
version outright -- `UnsupportedClassVersionError`, at load time, on the user's
machine rather than ours -- so quietly emitting something other than what was
asked for trades a build error here for a runtime failure there.

WHY A TABLE AND NOT ARITHMETIC. Since Java 5 the mapping has been
`major = release + 44`, so the table below could be a sum. It is written out
because the releases before that are not arithmetic at all (1.1 is 45, 1.4 is
48); because a table can be checked against the JVM specification line by line
and a sum cannot; and because a release nobody has heard of should be an error
rather than a plausible number. Releases PAST the newest entry are extrapolated
from the same rule -- see `_from_release` -- so a JDK that ships next year is
not blocked by an asmpython that predates it.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Java release -> the highest class-file major version that release accepts.
#:
#: "Highest", not "only": a JVM loads anything from 45 up to its own entry, so
#: this is the ceiling `--java-version` resolves to and the floor below which
#: nothing is gained by going lower.
JAVA_TO_CLASS_VERSION: dict[str, int] = {
    "1.1": 45, "1.2": 46, "1.3": 47, "1.4": 48,
    "1.5": 49, "5": 49,
    "1.6": 50, "6": 50,
    "1.7": 51, "7": 51,
    "1.8": 52, "8": 52,
    "9": 53, "10": 54, "11": 55, "12": 56, "13": 57, "14": 58,
    "15": 59, "16": 60, "17": 61, "18": 62, "19": 63, "20": 64,
    "21": 65, "22": 66, "23": 67, "24": 68, "25": 69, "26": 70,
}

#: The oldest version any JVM will load. Below this is not an old class file,
#: it is not a class file.
OLDEST = 45

#: The newest release the table names, and the version it maps to.
NEWEST_RELEASE = 26
NEWEST_KNOWN = JAVA_TO_CLASS_VERSION[str(NEWEST_RELEASE)]

#: `major = release + 44`. Derived from the table rather than written as 44, so
#: the two cannot disagree.
_OFFSET = NEWEST_KNOWN - NEWEST_RELEASE

#: How far past `NEWEST_KNOWN` a version may be asked for. Java ships two
#: releases a year, so this is a decade of headroom: enough that a JDK newer
#: than this compiler is never blocked, and tight enough that
#: `--class-version 650` is still caught as the typo it is.
HEADROOM = 20
NEWEST_ACCEPTED = NEWEST_KNOWN + HEADROOM

#: Written when neither option is given. Java 8 is the oldest version still
#: accepted by every JVM in service, so it is the choice that asks the least of
#: whoever runs the result.
DEFAULT = JAVA_TO_CLASS_VERSION["8"]

#: From this version the verifier reads `StackMapTable`; from 51 it requires
#: one on every method that branches. Below 50 the JVM infers frames itself.
#: `classfile.py` reads this to decide whether to write frames at all.
STACK_MAP_REQUIRED_FROM = 50


class VersionError(ValueError):
    """A version option nobody can act on. Carries a user-facing message."""


@dataclass(frozen=True, slots=True)
class ClassVersion:
    """The answer, plus enough context to explain itself."""

    #: What goes in the class-file header.
    major: int
    #: Which option decided it: "--class-version", "--java-version" or
    #: "default". Carried because every interesting diagnostic here is about
    #: WHICH option won, not about the number.
    source: str
    #: Advisory. Empty when there is nothing to say; never a reason to stop.
    note: str = ""

    @property
    def needs_stack_map(self) -> bool:
        return self.major >= STACK_MAP_REQUIRED_FROM

    @property
    def runs_on(self) -> str:
        """The oldest Java release that loads a class of this version.

        Reported rather than inferred by the reader: "65" means nothing to
        someone deciding whether their JVM will take it, and "Java 21" means
        everything.
        """
        for release, major in JAVA_TO_CLASS_VERSION.items():
            if major == self.major and not release.startswith("1."):
                return f"Java {release}"
        if self.major > NEWEST_KNOWN:
            return f"Java {self.major - _OFFSET} (extrapolated)"
        for release, major in JAVA_TO_CLASS_VERSION.items():
            if major == self.major:
                return f"Java {release}"
        return f"class-file version {self.major}"

    def __str__(self) -> str:
        return f"{self.major} ({self.runs_on})"


def resolve(*, class_version: str | int | None = None,
            java_version: str | int | None = None) -> ClassVersion:
    """Decide the class-file major version from the two options.

    `--class-version` takes priority, and says so in `note` when it is
    overriding a `--java-version` that asked for something else. Silent
    precedence is the failure mode here: someone who passes both and gets the
    other one's answer has no way to find out from the output.
    """
    if _given(class_version):
        major = _major(class_version)
        note = ""
        if _given(java_version):
            asked, _ = _from_release(java_version)
            if asked != major:
                note = (f"--class-version {major} takes priority over "
                        f"--java-version {java_version}, which asks for "
                        f"{asked}")
        elif major > NEWEST_KNOWN:
            note = (f"class-file version {major} is newer than any Java "
                    f"release this compiler knows (newest is {NEWEST_KNOWN}, "
                    f"Java {NEWEST_RELEASE})")
        return ClassVersion(major, "--class-version", note)

    if _given(java_version):
        major, note = _from_release(java_version)
        return ClassVersion(major, "--java-version", note)

    return ClassVersion(DEFAULT, "default")


def _given(value) -> bool:
    """An option is absent when it is None or empty, not when it is falsy.

    `--class-version 0` is a value; it is a wrong one, and it gets rejected
    with a message rather than treated as "not passed".
    """
    return value is not None and str(value).strip() != ""


def _major(value) -> int:
    text = str(value).strip()
    try:
        major = int(text)
    except ValueError:
        raise VersionError(
            f"--class-version wants a class-file major version, not {text!r}"
            f"\n(that is the number in the file header: {DEFAULT} is Java 8, "
            f"{JAVA_TO_CLASS_VERSION['21']} is Java 21)"
        ) from None
    if not OLDEST <= major <= NEWEST_ACCEPTED:
        raise VersionError(
            f"class-file version {major} is outside {OLDEST}..{NEWEST_ACCEPTED}"
            f"\n{OLDEST} is Java 1.1, the oldest any JVM loads; "
            f"{NEWEST_KNOWN} is Java {NEWEST_RELEASE}, the newest this "
            f"compiler has a release for, and everything up to "
            f"{NEWEST_ACCEPTED} is accepted so a newer JDK is not blocked"
        )
    return major


def _from_release(value) -> tuple[int, str]:
    """Look a Java release up in the table. Returns (major, note)."""
    key = str(value).strip()
    if key in JAVA_TO_CLASS_VERSION:
        return JAVA_TO_CLASS_VERSION[key], ""

    # `1.8` and `8` are the same release written two ways, and both appear in
    # the wild -- the table carries both spellings for the releases that had
    # one, so reaching here means the `1.x` form of something that never used
    # it, like `1.21`.
    if key.startswith("1.") and key[2:] in JAVA_TO_CLASS_VERSION:
        raise VersionError(
            f"unknown Java version {key!r}; Java stopped using the `1.x` "
            f"spelling after 1.8 -- write {key[2:]!r}")

    # A release past the table is extrapolated rather than refused. The rule
    # has held for every release since 5 and the alternative is a compiler that
    # cannot target a JDK released after it, which is a worse failure than a
    # number that turns out to be wrong in a way the JVM reports clearly.
    if key.isdigit() and int(key) > NEWEST_RELEASE:
        major = int(key) + _OFFSET
        if major > NEWEST_ACCEPTED:
            raise VersionError(
                f"Java {key} would be class-file version {major}, past the "
                f"{NEWEST_ACCEPTED} this compiler accepts"
                f"\npass --class-version {major} if you mean it")
        return major, (f"Java {key} is newer than this compiler knows; "
                       f"class-file version {major} is extrapolated from "
                       f"Java {NEWEST_RELEASE} = {NEWEST_KNOWN}")

    raise VersionError(
        f"unknown Java version {key!r}"
        f"\nknown: " + ", ".join(sorted(JAVA_TO_CLASS_VERSION, key=_sort_key)))


def _sort_key(release: str) -> tuple:
    parts = release.split(".")
    if all(p.isdigit() for p in parts):
        return tuple(int(p) for p in parts)
    return (999,)
