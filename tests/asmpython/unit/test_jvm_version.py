"""Which class-file version gets written.

Two options decide it and one of them wins, and the whole point of the pair is
that ordering:

    --java-version 21       -> 65, through the table
    --class-version 75      -> 75, whatever the table says
    both                    -> 75, and the user is told why

Every case below is about that precedence or about a number this must not
silently adjust. A compiler that quietly writes an older class than it was
asked for produces a jar that runs here and fails on the machine it was built
for, which is the failure this file exists to prevent.
"""
from __future__ import annotations

from tests import harness

from asmpython.backends.jvm import version as V


class TestTheTable:
    @harness.cases("release,major", [
        ("1.1", 45), ("1.4", 48), ("1.8", 52), ("8", 52), ("11", 55),
        ("17", 61), ("21", 65), ("25", 69), ("26", 70),
    ])
    def test_a_release_resolves_to_its_class_version(self, release, major):
        assert V.resolve(java_version=release).major == major

    def test_the_two_spellings_of_java_8_agree(self):
        assert (V.resolve(java_version="1.8").major
                == V.resolve(java_version="8").major)

    def test_every_entry_from_5_upward_is_the_release_plus_44(self):
        """The table is written out rather than computed, so this is what
        stops a typo in it from being invisible."""
        for release, major in V.JAVA_TO_CLASS_VERSION.items():
            if release.startswith("1.") or int(release) < 5:
                continue
            assert major == int(release) + 44, f"Java {release} -> {major}"

    def test_the_named_newest_matches_the_table(self):
        assert V.NEWEST_KNOWN == max(V.JAVA_TO_CLASS_VERSION.values())
        assert V.JAVA_TO_CLASS_VERSION[str(V.NEWEST_RELEASE)] == V.NEWEST_KNOWN


class TestPrecedence:
    """`--class-version` wins. This is the requested behaviour."""

    def test_class_version_alone_is_written_verbatim(self):
        assert V.resolve(class_version="75").major == 75

    def test_class_version_beats_java_version(self):
        got = V.resolve(class_version="75", java_version="21")
        assert got.major == 75
        assert got.source == "--class-version"

    def test_it_says_so_rather_than_winning_silently(self):
        """Someone who passes both and gets the other one's answer has no way
        to find that out from a class file."""
        got = V.resolve(class_version="75", java_version="21")
        assert "priority" in got.note
        assert "65" in got.note, "the note must name what was overridden"

    def test_agreeing_options_produce_no_note(self):
        assert V.resolve(class_version="65", java_version="21").note == ""

    def test_java_version_alone_still_works(self):
        got = V.resolve(java_version="21")
        assert (got.major, got.source) == (65, "--java-version")

    def test_neither_is_java_8(self):
        got = V.resolve()
        assert (got.major, got.source) == (52, "default")

    @harness.cases("empty", [None, "", "   "])
    def test_an_absent_option_is_absent_however_it_arrives(self, empty):
        assert V.resolve(class_version=empty, java_version="21").major == 65
        assert V.resolve(class_version="75", java_version=empty).major == 75


class TestTheFuture:
    """A JDK newer than this compiler must not be a wall."""

    def test_an_unreleased_java_extrapolates(self):
        got = V.resolve(java_version="31")
        assert got.major == 75
        assert "extrapolated" in got.note

    def test_a_class_version_past_the_table_is_allowed_and_flagged(self):
        got = V.resolve(class_version=str(V.NEWEST_KNOWN + 3))
        assert got.major == V.NEWEST_KNOWN + 3
        assert got.note, "a version with no known release should say so"

    def test_a_known_release_is_not_flagged(self):
        assert V.resolve(java_version="21").note == ""


class TestRejection:
    """Every one of these is a message, never a traceback."""

    @harness.cases("value", ["abc", "", "0", "44", "1000", "-1", "52.0"])
    def test_a_bad_class_version_is_a_version_error(self, value):
        if value == "":
            harness.skip("empty means absent, covered above")
        with harness.raises(V.VersionError):
            V.resolve(class_version=value)

    @harness.cases("value", ["banana", "1.21", "4", "0"])
    def test_a_bad_java_version_is_a_version_error(self, value):
        with harness.raises(V.VersionError):
            V.resolve(java_version=value)

    def test_the_1x_spelling_of_a_modern_release_says_what_to_write(self):
        with harness.raises(V.VersionError, match="21"):
            V.resolve(java_version="1.21")

    def test_an_unknown_release_lists_the_known_ones(self):
        try:
            V.resolve(java_version="banana")
        except V.VersionError as exc:
            assert "21" in str(exc) and "1.8" in str(exc)
        else:
            harness.fail("expected a VersionError")

    def test_a_java_release_beyond_the_ceiling_suggests_the_escape_hatch(self):
        with harness.raises(V.VersionError, match="--class-version"):
            V.resolve(java_version="500")

    @harness.cases("edge", [V.OLDEST, V.NEWEST_ACCEPTED])
    def test_the_boundaries_themselves_are_accepted(self, edge):
        assert V.resolve(class_version=str(edge)).major == edge

    @harness.cases("edge", [V.OLDEST - 1, V.NEWEST_ACCEPTED + 1])
    def test_one_past_each_boundary_is_not(self, edge):
        with harness.raises(V.VersionError):
            V.resolve(class_version=str(edge))


class TestWhatItReportsBack:
    def test_a_version_names_the_release_that_loads_it(self):
        assert V.resolve(java_version="21").runs_on == "Java 21"
        assert V.resolve(class_version="52").runs_on == "Java 8"

    def test_an_extrapolated_version_says_it_is_one(self):
        assert "extrapolated" in V.resolve(class_version="75").runs_on

    def test_stack_maps_are_required_from_50(self):
        assert not V.resolve(class_version="49").needs_stack_map
        assert V.resolve(class_version="50").needs_stack_map
        assert V.resolve(java_version="21").needs_stack_map
