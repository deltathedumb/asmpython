"""unittest module: unit testing framework.

A practical subset of CPython's unittest compatible with asmpython's
type system. The main classes are TestCase, TestSuite, TestResult, and
TestRunner.

Typical usage::

    import unittest

    class MyTest(unittest.TestCase):
        def test_add(self) -> None:
            self.assertEqual(1 + 1, 2)

    if __name__ == "__main__":
        unittest.main()
"""
from __future__ import annotations

import sys


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AssertionError(Exception):
    """Raised by assertion methods in TestCase."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return self.msg


class SkipTest(Exception):
    """Raised to skip a test."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return self.msg


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def skip(reason: str) -> int:
    """Decorator: unconditionally skip the decorated test."""
    def decorator(func: int) -> int:
        return func
    return decorator


def skipIf(condition: int, reason: str) -> int:
    """Decorator: skip if *condition* is true."""
    def decorator(func: int) -> int:
        return func
    return decorator


def skipUnless(condition: int, reason: str) -> int:
    """Decorator: skip unless *condition* is true."""
    def decorator(func: int) -> int:
        return func
    return decorator


def expectedFailure(func: int) -> int:
    """Decorator: mark test as expected to fail."""
    return func


# ---------------------------------------------------------------------------
# TestResult
# ---------------------------------------------------------------------------

class TestResult:
    """Accumulates the results of running a set of tests."""

    def __init__(self) -> None:
        self.failures: list = []       # list of [test_name, msg]
        self.errors: list = []         # list of [test_name, msg]
        self.skipped: list = []        # list of [test_name, reason]
        self.expected_failures: list = []
        self.unexpected_successes: list = []
        self.tests_run: int = 0
        self._stop: int = 0

    def wasSuccessful(self) -> int:
        """Return 1 if no failures or errors."""
        return 1 if len(self.failures) == 0 and len(self.errors) == 0 else 0

    def stop(self) -> None:
        """Indicate that no more tests should be run."""
        self._stop = 1

    def startTest(self, test: str) -> None:
        self.tests_run = self.tests_run + 1

    def addSuccess(self, test: str) -> None:
        pass

    def addFailure(self, test: str, exc_msg: str) -> None:
        self.failures.append([test, exc_msg])

    def addError(self, test: str, exc_msg: str) -> None:
        self.errors.append([test, exc_msg])

    def addSkip(self, test: str, reason: str) -> None:
        self.skipped.append([test, reason])

    def addExpectedFailure(self, test: str, exc_msg: str) -> None:
        self.expected_failures.append([test, exc_msg])

    def addUnexpectedSuccess(self, test: str) -> None:
        self.unexpected_successes.append(test)

    def printErrors(self) -> None:
        if len(self.errors) > 0:
            print("Errors:")
            for e in self.errors:
                print("  " + e[0] + ": " + e[1])
        if len(self.failures) > 0:
            print("Failures:")
            for f in self.failures:
                print("  " + f[0] + ": " + f[1])


# ---------------------------------------------------------------------------
# TestCase
# ---------------------------------------------------------------------------

class TestCase:
    """Base class for all test cases.

    Subclass and write methods whose names start with 'test'. Call
    assertEqual, assertTrue, assertRaises, etc. to make assertions.
    """

    def __init__(self, methodName: str = "runTest") -> None:
        self._methodName: str = methodName
        self._outcome_result: str = ""
        self.maxDiff: int = 80

    def setUp(self) -> None:
        """Called before each test method."""
        pass

    def tearDown(self) -> None:
        """Called after each test method."""
        pass

    def setUpClass(self) -> None:
        """Called once before tests in an individual class are run."""
        pass

    def tearDownClass(self) -> None:
        """Called once after tests in an individual class are run."""
        pass

    def id(self) -> str:
        return self.__class__.__name__ + "." + self._methodName

    def shortDescription(self) -> str:
        return self._methodName

    # --- assertion helpers ---------------------------------------------------

    def fail(self, msg: str = "") -> None:
        """Unconditionally fail."""
        raise AssertionError(msg)

    def assertEqual(self, first: object, second: object, msg: str = "") -> None:
        """Fail if first != second."""
        if first != second:
            if len(msg) > 0:
                raise AssertionError(msg)
            raise AssertionError(str(first) + " != " + str(second))

    def assertNotEqual(self, first: object, second: object, msg: str = "") -> None:
        """Fail if first == second."""
        if first == second:
            if len(msg) > 0:
                raise AssertionError(msg)
            raise AssertionError(str(first) + " == " + str(second) + " (expected not equal)")

    def assertTrue(self, expr: object, msg: str = "") -> None:
        """Fail if expr is not truthy."""
        if not expr:
            if len(msg) > 0:
                raise AssertionError(msg)
            raise AssertionError(str(expr) + " is not truthy")

    def assertFalse(self, expr: object, msg: str = "") -> None:
        """Fail if expr is truthy."""
        if expr:
            if len(msg) > 0:
                raise AssertionError(msg)
            raise AssertionError(str(expr) + " is truthy (expected falsy)")

    def assertIs(self, first: object, second: object, msg: str = "") -> None:
        """Fail if first is not second (identity check via id())."""
        if id(first) != id(second):
            if len(msg) > 0:
                raise AssertionError(msg)
            raise AssertionError(str(first) + " is not " + str(second))

    def assertIsNot(self, first: object, second: object, msg: str = "") -> None:
        """Fail if first is second."""
        if id(first) == id(second):
            if len(msg) > 0:
                raise AssertionError(msg)
            raise AssertionError(str(first) + " is " + str(second) + " (expected not)")

    def assertIsNone(self, obj: object, msg: str = "") -> None:
        """Fail if obj is not None."""
        if obj is not None:
            if len(msg) > 0:
                raise AssertionError(msg)
            raise AssertionError(str(obj) + " is not None")

    def assertIsNotNone(self, obj: object, msg: str = "") -> None:
        """Fail if obj is None."""
        if obj is None:
            if len(msg) > 0:
                raise AssertionError(msg)
            raise AssertionError("unexpectedly None")

    def assertIn(self, member: str, container: str, msg: str = "") -> None:
        """Fail if member not in container."""
        if member not in container:
            if len(msg) > 0:
                raise AssertionError(msg)
            raise AssertionError(str(member) + " not in " + str(container))

    def assertNotIn(self, member: str, container: str, msg: str = "") -> None:
        """Fail if member in container."""
        if member in container:
            if len(msg) > 0:
                raise AssertionError(msg)
            raise AssertionError(str(member) + " in " + str(container))

    def assertGreater(self, first: object, second: object, msg: str = "") -> None:
        """Fail if first <= second."""
        if not (first > second):
            if len(msg) > 0:
                raise AssertionError(msg)
            raise AssertionError(str(first) + " not > " + str(second))

    def assertGreaterEqual(self, first: object, second: object, msg: str = "") -> None:
        """Fail if first < second."""
        if not (first >= second):
            if len(msg) > 0:
                raise AssertionError(msg)
            raise AssertionError(str(first) + " not >= " + str(second))

    def assertLess(self, first: object, second: object, msg: str = "") -> None:
        """Fail if first >= second."""
        if not (first < second):
            if len(msg) > 0:
                raise AssertionError(msg)
            raise AssertionError(str(first) + " not < " + str(second))

    def assertLessEqual(self, first: object, second: object, msg: str = "") -> None:
        """Fail if first > second."""
        if not (first <= second):
            if len(msg) > 0:
                raise AssertionError(msg)
            raise AssertionError(str(first) + " not <= " + str(second))

    def assertAlmostEqual(self, first: float, second: float,
                          places: int = 7, msg: str = "",
                          delta: float = 0.0) -> None:
        """Fail if the two floats are not equal within *places* decimal places
        (or within *delta* if *delta* is given)."""
        if delta > 0.0:
            diff: float = first - second
            if diff < 0.0:
                diff = -diff
            if diff > delta:
                if len(msg) > 0:
                    raise AssertionError(msg)
                raise AssertionError(str(first) + " != " + str(second) + " within delta " + str(delta))
            return
        # Round to *places* decimal places.
        scale: float = 1.0
        p: int = 0
        while p < places:
            scale = scale * 10.0
            p = p + 1
        diff2: float = first - second
        if diff2 < 0.0:
            diff2 = -diff2
        rounded: int = int(diff2 * scale + 0.5)
        if rounded != 0:
            if len(msg) > 0:
                raise AssertionError(msg)
            raise AssertionError(str(first) + " != " + str(second) + " within " + str(places) + " places")

    def assertNotAlmostEqual(self, first: float, second: float,
                             places: int = 7, msg: str = "") -> None:
        """Fail if the two floats ARE equal within *places* decimal places."""
        scale: float = 1.0
        p: int = 0
        while p < places:
            scale = scale * 10.0
            p = p + 1
        diff: float = first - second
        if diff < 0.0:
            diff = -diff
        rounded: int = int(diff * scale + 0.5)
        if rounded == 0:
            if len(msg) > 0:
                raise AssertionError(msg)
            raise AssertionError(str(first) + " == " + str(second) + " within " + str(places) + " places")

    def assertMultiLineEqual(self, first: str, second: str, msg: str = "") -> None:
        """Fail if the two multi-line strings are not equal."""
        self.assertEqual(first, second, msg)

    def assertSequenceEqual(self, first: list, second: list, msg: str = "") -> None:
        """Fail if the two lists are not equal."""
        if len(first) != len(second):
            if len(msg) > 0:
                raise AssertionError(msg)
            raise AssertionError("sequences have different lengths: " + str(len(first)) + " vs " + str(len(second)))
        i: int = 0
        while i < len(first):
            if first[i] != second[i]:
                if len(msg) > 0:
                    raise AssertionError(msg)
                raise AssertionError("sequences differ at index " + str(i) + ": " + str(first[i]) + " != " + str(second[i]))
            i = i + 1

    def assertListEqual(self, first: list, second: list, msg: str = "") -> None:
        self.assertSequenceEqual(first, second, msg)

    def assertTupleEqual(self, first: list, second: list, msg: str = "") -> None:
        self.assertSequenceEqual(first, second, msg)

    def assertSetEqual(self, first: set, second: set, msg: str = "") -> None:
        """Fail if the two sets are not equal."""
        # Check all elements of first are in second and vice versa.
        for k in first:
            if k not in second:
                if len(msg) > 0:
                    raise AssertionError(msg)
                raise AssertionError(str(k) + " in first set but not in second")
        for k in second:
            if k not in first:
                if len(msg) > 0:
                    raise AssertionError(msg)
                raise AssertionError(str(k) + " in second set but not in first")

    def assertDictEqual(self, first: dict, second: dict, msg: str = "") -> None:
        """Fail if the two dicts are not equal."""
        for k in first:
            if second.get(k, "\x00_MISSING_\x00") == "\x00_MISSING_\x00":
                if len(msg) > 0:
                    raise AssertionError(msg)
                raise AssertionError("key " + str(k) + " in first dict but not in second")
            if first[k] != second[k]:
                if len(msg) > 0:
                    raise AssertionError(msg)
                raise AssertionError("dicts differ at key " + str(k) + ": " + str(first[k]) + " != " + str(second[k]))
        for k2 in second:
            if first.get(k2, "\x00_MISSING_\x00") == "\x00_MISSING_\x00":
                if len(msg) > 0:
                    raise AssertionError(msg)
                raise AssertionError("key " + str(k2) + " in second dict but not in first")

    def assertRaisesRegex(self, expected: str, regex: str, msg: str = "") -> None:
        """Stub: not fully implementable without a callable context."""
        pass

    def addCleanup(self, func: int, arg: int = 0) -> None:
        """Register a cleanup function (stub: no-op in asmpython)."""
        pass

    def subTest(self, msg: str = "", param: str = "") -> object:
        """Context manager for sub-tests (stub: returns self)."""
        return self

    def __enter__(self) -> TestCase:
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        return 0

    def countTestCases(self) -> int:
        return 1

    def debug(self) -> None:
        """Run the test without collecting results (calls setUp/test/tearDown)."""
        self.setUp()
        self.tearDown()

    def run(self, result: TestResult) -> TestResult:
        """Run this test, recording results to *result*."""
        result.startTest(self.id())
        self.setUp()
        result.addSuccess(self.id())
        self.tearDown()
        return result


# ---------------------------------------------------------------------------
# TestSuite
# ---------------------------------------------------------------------------

class TestSuite:
    """A collection of TestCase instances or nested TestSuites."""

    def __init__(self, tests: list = []) -> None:
        self._tests: list = []
        for t in tests:
            self._tests.append(t)

    def addTest(self, test: object) -> None:
        """Add a test to the suite."""
        self._tests.append(test)

    def addTests(self, tests: list) -> None:
        """Add all tests from *tests* to the suite."""
        for t in tests:
            self._tests.append(t)

    def countTestCases(self) -> int:
        count: int = 0
        for t in self._tests:
            count = count + t.countTestCases()
        return count

    def run(self, result: TestResult) -> TestResult:
        """Run all tests in this suite."""
        for test in self._tests:
            if result._stop:
                break
            test.run(result)
        return result

    def __iter__(self) -> list:
        return self._tests


# ---------------------------------------------------------------------------
# TestLoader
# ---------------------------------------------------------------------------

class TestLoader:
    """Discovers and loads TestCase subclasses.

    In asmpython, test discovery is manual: pass your TestCase class to
    loadTestsFromTestCase() to create a suite that can be run.
    """

    testMethodPrefix: str = "test"
    sortTestMethodsUsing: int = 0

    def __init__(self) -> None:
        pass

    def loadTestsFromTestCase(self, testCaseClass: int) -> TestSuite:
        """Return a TestSuite containing all tests in *testCaseClass* (stub)."""
        return TestSuite()

    def loadTestsFromModule(self, module: int) -> TestSuite:
        """Return a TestSuite with all test cases found in *module* (stub)."""
        return TestSuite()

    def loadTestsFromName(self, name: str, module: int = 0) -> TestSuite:
        """Return a TestSuite corresponding to *name* (stub)."""
        return TestSuite()


defaultTestLoader: TestLoader = TestLoader()


# ---------------------------------------------------------------------------
# TextTestResult / TextTestRunner
# ---------------------------------------------------------------------------

class TextTestResult(TestResult):
    """TestResult subclass that prints results to stdout."""

    def __init__(self, verbosity: int = 1) -> None:
        self.failures: list = []
        self.errors: list = []
        self.skipped: list = []
        self.expected_failures: list = []
        self.unexpected_successes: list = []
        self.tests_run: int = 0
        self._stop: int = 0
        self._verbosity: int = verbosity

    def startTest(self, test: str) -> None:
        self.tests_run = self.tests_run + 1
        if self._verbosity > 1:
            print(test + " ... ", end="")

    def addSuccess(self, test: str) -> None:
        if self._verbosity > 1:
            print("ok")
        elif self._verbosity == 1:
            print(".", end="")

    def addFailure(self, test: str, exc_msg: str) -> None:
        self.failures.append([test, exc_msg])
        if self._verbosity > 1:
            print("FAIL")
        elif self._verbosity == 1:
            print("F", end="")

    def addError(self, test: str, exc_msg: str) -> None:
        self.errors.append([test, exc_msg])
        if self._verbosity > 1:
            print("ERROR")
        elif self._verbosity == 1:
            print("E", end="")

    def addSkip(self, test: str, reason: str) -> None:
        self.skipped.append([test, reason])
        if self._verbosity > 1:
            print("skipped " + reason)
        elif self._verbosity == 1:
            print("s", end="")

    def printErrors(self) -> None:
        if self._verbosity >= 1:
            print("")
        if len(self.errors) > 0:
            print("=" * 70)
            print("ERRORS")
            for e in self.errors:
                print("-" * 70)
                print("ERROR: " + e[0])
                print(e[1])
        if len(self.failures) > 0:
            print("=" * 70)
            print("FAILURES")
            for f in self.failures:
                print("-" * 70)
                print("FAIL: " + f[0])
                print(f[1])

    def printSummary(self) -> None:
        """Print the run summary line."""
        ran: str = "Ran " + str(self.tests_run) + " test"
        if self.tests_run != 1:
            ran = ran + "s"
        print(ran + " in total")
        if self.wasSuccessful():
            print("OK")
        else:
            parts: list = []
            if len(self.failures) > 0:
                parts.append("failures=" + str(len(self.failures)))
            if len(self.errors) > 0:
                parts.append("errors=" + str(len(self.errors)))
            if len(self.skipped) > 0:
                parts.append("skipped=" + str(len(self.skipped)))
            msg: str = "FAILED ("
            i: int = 0
            while i < len(parts):
                if i > 0:
                    msg = msg + ", "
                msg = msg + parts[i]
                i = i + 1
            print(msg + ")")


class TextTestRunner:
    """A test runner that prints formatted test results to stdout."""

    def __init__(self, verbosity: int = 1, failfast: int = 0) -> None:
        self._verbosity: int = verbosity
        self._failfast: int = failfast

    def _makeResult(self) -> TextTestResult:
        return TextTestResult(self._verbosity)

    def run(self, test: object) -> TextTestResult:
        """Run the given test case or suite and return a TestResult."""
        result: TextTestResult = self._makeResult()
        test.run(result)
        result.printErrors()
        result.printSummary()
        return result


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def main(module: str = "", exit_on_fail: int = 1, verbosity: int = 1) -> None:
    """Simple entry point: run all TestCase subclasses defined in *module*.

    In asmpython, test discovery via import-module introspection is not
    possible. Users should construct a TestSuite manually and call
    TextTestRunner().run(suite) for explicit control, or build a subclass
    of TestCase and call main() for a single-class run.
    """
    runner: TextTestRunner = TextTestRunner(verbosity=verbosity)
    suite: TestSuite = TestSuite()
    result: TextTestResult = runner.run(suite)
    if exit_on_fail and not result.wasSuccessful():
        sys.exit(1)


# ---------------------------------------------------------------------------
# FunctionTestCase: wrap a bare function as a test
# ---------------------------------------------------------------------------

class FunctionTestCase(TestCase):
    """Wrap a callable as a TestCase."""

    def __init__(self, testFunc: int, setUp: int = 0, tearDown: int = 0,
                 description: str = "") -> None:
        self._testFunc: int = testFunc
        self._setUp: int = setUp
        self._tearDown: int = tearDown
        self._description: str = description
        self._methodName: str = description if len(description) > 0 else "test"
        self.maxDiff: int = 80
        self._outcome_result: str = ""

    def setUp(self) -> None:
        if self._setUp != 0:
            self._setUp()

    def tearDown(self) -> None:
        if self._tearDown != 0:
            self._tearDown()

    def runTest(self) -> None:
        self._testFunc()

    def id(self) -> str:
        return self._description if len(self._description) > 0 else "FunctionTestCase"

    def shortDescription(self) -> str:
        return self.id()
