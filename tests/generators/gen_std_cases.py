"""Generate stdlib-binding conformance probes.

asmpython does not run CPython's stdlib. `asmpython/stdlib/` re-declares each
module -- some as FFI signature tables (`math` binds straight to libm), some as
re-implementations in asmpython-compatible Python (`string`, `collections`).
Either way the binding is a *hand-written restatement* of an API, and the ways
a restatement drifts from the original are mechanical:

* the function is bound but its signature is wrong -- an optional argument is
  missing, a keyword-only argument is positional, a variadic form is fixed-arity
* the function is absent from an otherwise-bound module
* the module has no bindings at all
* the function exists and returns the right value with the wrong *type*
  (`statistics.median` of ints returning a float, `json.loads` yielding strs)

FAILURE_AUDIT.md attributes ~55 known failures to exactly these four shapes,
and the corpus had no probe isolating any of them: the `lib_*.py` cases are
ordinary programs that happen to import a module, so a failure says "this
program is wrong" and not "this signature is wrong".

Each probe here pins ONE binding contract. Where a real CPython API is
inherently nondeterministic (`random`), the probe asserts the part of the
contract that is deterministic -- that `randrange(0, 10, 2)` honours the step,
not which value it drew -- so a failure means the binding is wrong rather than
that two RNGs disagree.

Usage: python gen_std_cases.py <tests/cases dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _emit import CaseSet, main  # noqa: E402

CASES = CaseSet("probes")
case = CASES.case


# ---------------------------------------------------------------------------
# math -- the module with the largest FFI binding table, and the one where a
# missing optional argument is most likely (libm has no variadic forms).
# ---------------------------------------------------------------------------

case("std_math_prod", "math.prod folds a sequence to a product", r'''
import math

print(math.prod([1, 2, 3, 4]))
print(math.prod([]))
''')

case("std_math_prod_start", "math.prod accepts the start= keyword", r'''
import math

print(math.prod([2, 5], start=3))
''')

case("std_math_dist", "math.dist measures between two point sequences", r'''
import math

print(math.dist((0.0, 0.0), (3.0, 4.0)))
''')

case("std_math_isqrt", "math.isqrt returns the integer square root", r'''
import math

print(math.isqrt(17))
print(math.isqrt(16))
''')

case("std_math_gcd_variadic", "math.gcd accepts more than two arguments", r'''
import math

print(math.gcd(12, 18, 24))
''')

case("std_math_lcm", "math.lcm exists and is variadic", r'''
import math

print(math.lcm(4, 6))
print(math.lcm(2, 3, 4))
''')

case("std_math_comb", "math.comb computes binomial coefficients", r'''
import math

print(math.comb(5, 2))
print(math.comb(10, 0))
''')

case("std_math_perm", "math.perm computes permutation counts", r'''
import math

print(math.perm(5, 2))
''')

case("std_math_fsum", "math.fsum sums without accumulating float error", r'''
import math

print(math.fsum([0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]))
''')

case("std_math_isclose_kwargs", "math.isclose accepts rel_tol/abs_tol keywords", r'''
import math

print(math.isclose(1.0, 1.000001, rel_tol=1e-3))
print(math.isclose(1.0, 1.5, rel_tol=1e-3))
print(math.isclose(0.0, 1e-9, abs_tol=1e-6))
''')

# NOTE: not named `std_math_log_base` -- .gitignore carries a blanket `*log*`
# rule, which silently makes any case file with "log" in its name untrackable.
# The generator writes it, the runner runs it, and `git add` ignores it.
case("std_math_base_argument", "math.log accepts an optional second base argument", r'''
import math

print(math.log(8.0, 2.0))
print(math.log(100.0, 10.0))
''')

case("std_math_floor_returns_int", "math.floor/ceil return int, not float", r'''
import math

print(type(math.floor(2.7)).__name__)
print(type(math.ceil(2.1)).__name__)
''')

case("std_math_hypot_variadic", "math.hypot accepts more than two coordinates", r'''
import math

print(math.hypot(3.0, 4.0))
print(math.hypot(1.0, 2.0, 2.0))
''')

case("std_math_factorial", "math.factorial exists", r'''
import math

print(math.factorial(5))
print(math.factorial(0))
''')

case("std_math_modf", "math.modf returns a (frac, int) pair", r'''
import math

parts = math.modf(3.5)
print(parts[0])
print(parts[1])
''')

case("std_math_sumprod", "math.sumprod exists (3.12+)", r'''
import math

print(math.sumprod([1, 2, 3], [4, 5, 6]))
''')

case("std_math_degrees_radians", "math.degrees/radians convert angles", r'''
import math

print(math.degrees(math.pi))
print(round(math.radians(180.0), 6) == round(math.pi, 6))
''')


# ---------------------------------------------------------------------------
# statistics -- return TYPE is the contract most easily lost here: CPython's
# median of an odd-length int sequence is the element itself, an int.
# ---------------------------------------------------------------------------

case("std_statistics_mean", "statistics.mean averages a sequence", r'''
import statistics

print(statistics.mean([1, 2, 3, 4]))
''')

case("std_statistics_median_type", "median of odd ints returns the int element", r'''
import statistics

m = statistics.median([1, 3, 5])
print(m)
print(type(m).__name__)
''')

case("std_statistics_mode", "statistics.mode returns the most common value", r'''
import statistics

print(statistics.mode([1, 2, 2, 3]))
''')

case("std_statistics_stdev", "statistics.stdev computes the sample deviation", r'''
import statistics

print(statistics.stdev([2, 4, 4, 4, 5, 5, 7, 9]))
''')

case("std_statistics_fmean", "statistics.fmean exists and returns float", r'''
import statistics

print(statistics.fmean([1, 2, 3, 4]))
''')


# ---------------------------------------------------------------------------
# random -- every probe asserts a signature/behaviour invariant that holds for
# ANY correct RNG, never a drawn value. A probe that pinned the stream would
# fail against a correct implementation with a different generator.
# ---------------------------------------------------------------------------

case("std_random_randrange_step", "randrange(start, stop, step) honours the step", r'''
import random

random.seed(1)
for _ in range(10):
    v = random.randrange(0, 10, 2)
    if v % 2 != 0 or v < 0 or v >= 10:
        print("out of range", v)
print("all even and in range")
''')

case("std_random_randint_inclusive", "randint's upper bound is inclusive", r'''
import random

random.seed(2)
seen_low = False
seen_high = False
for _ in range(200):
    v = random.randint(1, 2)
    if v == 1:
        seen_low = True
    elif v == 2:
        seen_high = True
    else:
        print("out of range", v)
print(seen_low)
print(seen_high)
''')

case("std_random_sample_distinct", "random.sample draws k distinct elements", r'''
import random

random.seed(3)
picked = random.sample([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], 3)
print(len(picked))
print(len(set(picked)))
print(sorted(picked) == sorted(set(picked)))
''')

case("std_random_shuffle_is_permutation", "random.shuffle permutes in place", r'''
import random

random.seed(4)
items = [1, 2, 3, 4, 5]
random.shuffle(items)
print(len(items))
print(sorted(items))
''')

case("std_random_uniform_bounds", "random.uniform stays within its bounds", r'''
import random

random.seed(5)
ok = True
for _ in range(50):
    v = random.uniform(1.0, 2.0)
    if v < 1.0 or v > 2.0:
        ok = False
print(ok)
''')

case("std_random_gauss_returns_float", "random.gauss exists and returns a float", r'''
import random

random.seed(6)
print(type(random.gauss(0.0, 1.0)).__name__)
''')

case("std_random_choice_from_list", "random.choice returns a member of its input", r'''
import random

random.seed(7)
options = ["a", "b", "c"]
ok = True
for _ in range(30):
    if random.choice(options) not in options:
        ok = False
print(ok)
''')


# ---------------------------------------------------------------------------
# time / datetime -- fixed instants only, so the expectation is machine- and
# timezone-independent (gmtime(0) is 1970-01-01T00:00:00Z everywhere).
# ---------------------------------------------------------------------------

case("std_time_strftime_gmtime", "time.strftime formats a struct_time", r'''
import time

print(time.strftime("%Y-%m-%d", time.gmtime(0)))
''')

case("std_time_gmtime_fields", "struct_time exposes named fields", r'''
import time

t = time.gmtime(0)
print(t.tm_year)
print(t.tm_mon)
print(t.tm_mday)
''')

case("std_time_struct_indexable", "struct_time is indexable like a tuple", r'''
import time

t = time.gmtime(86400)
print(t[0])
print(t[2])
''')

case("std_datetime_date_isoformat", "date.isoformat renders YYYY-MM-DD", r'''
import datetime

print(datetime.date(2020, 1, 2).isoformat())
''')

case("std_datetime_date_difference", "date - date yields a timedelta in days", r'''
import datetime

delta = datetime.date(2020, 3, 1) - datetime.date(2020, 2, 1)
print(delta.days)
''')

case("std_datetime_timedelta_add", "date + timedelta advances the date", r'''
import datetime

print((datetime.date(2020, 12, 31) + datetime.timedelta(days=1)).isoformat())
''')

case("std_datetime_date_weekday", "date.weekday is Monday-zero", r'''
import datetime

print(datetime.date(2020, 1, 6).weekday())
''')

case("std_datetime_strftime", "datetime.strftime honours its format string", r'''
import datetime

print(datetime.datetime(2021, 7, 4, 13, 5, 9).strftime("%Y/%m/%d %H:%M:%S"))
''')


# ---------------------------------------------------------------------------
# hashlib / hmac -- audit rank 7 (wrong signature) and rank 15 (no bindings).
# ---------------------------------------------------------------------------

case("std_hashlib_md5_hexdigest", "hashlib.md5 accepts data in the constructor", r'''
import hashlib

print(hashlib.md5(b"abc").hexdigest())
''')

case("std_hashlib_sha256_hexdigest", "hashlib.sha256 accepts data in the constructor", r'''
import hashlib

print(hashlib.sha256(b"abc").hexdigest())
''')

case("std_hashlib_update_accumulates", "hash.update appends to the running digest", r'''
import hashlib

h = hashlib.sha1()
h.update(b"ab")
h.update(b"c")
print(h.hexdigest() == hashlib.sha1(b"abc").hexdigest())
''')

case("std_hashlib_digest_size", "a hash object reports its digest_size", r'''
import hashlib

print(hashlib.sha256().digest_size)
''')

case("std_hmac_hexdigest", "hmac.new(key, msg, digestmod) works", r'''
import hashlib
import hmac

print(hmac.new(b"key", b"message", hashlib.sha256).hexdigest())
''')


# ---------------------------------------------------------------------------
# json -- the element TYPE of a parsed array is the contract that broke in
# lib_json_roundtrip (ints came back as strs).
# ---------------------------------------------------------------------------

case("std_json_loads_int_type", "json.loads yields ints, not strings", r'''
import json

parsed = json.loads("[1, 2, 3]")
print(parsed)
print(type(parsed[0]).__name__)
''')

case("std_json_loads_nested_types", "json.loads preserves each nested value kind", r'''
import json

obj = json.loads('{"n": 1, "f": 2.5, "s": "x", "b": true, "z": null}')
print(type(obj["n"]).__name__)
print(type(obj["f"]).__name__)
print(type(obj["s"]).__name__)
print(obj["b"])
print(obj["z"])
''')

case("std_json_dumps_sort_keys", "json.dumps accepts the sort_keys keyword", r'''
import json

print(json.dumps({"b": 1, "a": 2}, sort_keys=True))
''')

case("std_json_dumps_separators", "json.dumps accepts the separators keyword", r'''
import json

print(json.dumps([1, 2], separators=(",", ":")))
''')

case("std_json_dumps_indent", "json.dumps accepts the indent keyword", r'''
import json

print(json.dumps({"a": 1}, indent=2))
''')

case("std_json_roundtrip_identity", "dumps/loads round-trips a nested structure", r'''
import json

original = {"xs": [1, 2], "s": "t"}
print(json.loads(json.dumps(original)) == original)
''')


# ---------------------------------------------------------------------------
# itertools -- keyword-only arguments are the recurring break here
# (product(repeat=), zip_longest(fillvalue=), accumulate(initial=)).
# ---------------------------------------------------------------------------

case("std_itertools_product_repeat", "itertools.product accepts repeat=", r'''
import itertools

print(list(itertools.product([0, 1], repeat=2)))
''')

case("std_itertools_zip_longest_fillvalue", "zip_longest accepts fillvalue=", r'''
import itertools

print(list(itertools.zip_longest([1, 2, 3], ["a"], fillvalue="-")))
''')

case("std_itertools_accumulate_initial", "accumulate accepts initial=", r'''
import itertools

print(list(itertools.accumulate([1, 2, 3], initial=10)))
''')

case("std_itertools_islice_start_stop", "islice accepts start and stop", r'''
import itertools

print(list(itertools.islice(range(10), 2, 5)))
''')

case("std_itertools_islice_step", "islice accepts a step argument", r'''
import itertools

print(list(itertools.islice(range(10), 0, 10, 3)))
''')

case("std_itertools_permutations_r", "permutations accepts the length argument", r'''
import itertools

print(list(itertools.permutations([1, 2, 3], 2)))
''')

case("std_itertools_count_step", "count accepts a step argument", r'''
import itertools

print(list(itertools.islice(itertools.count(5, 3), 4)))
''')

case("std_itertools_starmap", "starmap spreads each tuple over the callable", r'''
import itertools

print(list(itertools.starmap(max, [(1, 2), (5, 3)])))
''')

case("std_itertools_pairwise", "itertools.pairwise exists (3.10+)", r'''
import itertools

print(list(itertools.pairwise([1, 2, 3, 4])))
''')

case("std_itertools_batched", "itertools.batched exists (3.12+)", r'''
import itertools

print([list(b) for b in itertools.batched([1, 2, 3, 4, 5], 2)])
''')

case("std_itertools_takewhile_dropwhile", "takewhile/dropwhile split on a predicate", r'''
import itertools

xs = [1, 2, 5, 1]
print(list(itertools.takewhile(lambda v: v < 3, xs)))
print(list(itertools.dropwhile(lambda v: v < 3, xs)))
''')

case("std_itertools_chain_from_iterable", "chain.from_iterable flattens one level", r'''
import itertools

print(list(itertools.chain.from_iterable([[1, 2], [3]])))
''')


# ---------------------------------------------------------------------------
# functools
# ---------------------------------------------------------------------------

case("std_functools_reduce_no_initial", "reduce works without an initial value", r'''
import functools

print(functools.reduce(lambda a, b: a + b, [1, 2, 3, 4]))
''')

case("std_functools_partial_extra_args", "partial prepends its bound arguments", r'''
import functools


def join3(a, b, c):
    return a + "-" + b + "-" + c


bound = functools.partial(join3, "x")
print(bound("y", "z"))
''')

case("std_functools_lru_cache_caches", "lru_cache stops re-entering the function", r'''
import functools

calls = []


@functools.lru_cache(maxsize=None)
def square(n):
    calls.append(n)
    return n * n


print(square(4))
print(square(4))
print(len(calls))
''')

case("std_functools_cache_decorator", "functools.cache exists (3.9+)", r'''
import functools


@functools.cache
def twice(n):
    return n * 2


print(twice(5))
print(twice(5))
''')


# ---------------------------------------------------------------------------
# collections
# ---------------------------------------------------------------------------

case("std_collections_counter_elements", "Counter.elements repeats each key", r'''
import collections

print(sorted(collections.Counter({"a": 2, "b": 1}).elements()))
''')

case("std_collections_deque_appendleft", "deque.appendleft prepends", r'''
import collections

d = collections.deque([2, 3])
d.appendleft(1)
print(list(d))
''')

case("std_collections_namedtuple_defaults", "namedtuple accepts defaults=", r'''
import collections

Point = collections.namedtuple("Point", "x y", defaults=(0,))
print(Point(1))
''')

case("std_collections_chainmap", "ChainMap searches its maps in order", r'''
import collections

merged = collections.ChainMap({"a": 1}, {"a": 2, "b": 3})
print(merged["a"])
print(merged["b"])
''')

case("std_collections_ordereddict_popitem_last", "OrderedDict.popitem accepts last=", r'''
import collections

d = collections.OrderedDict()
d["a"] = 1
d["b"] = 2
print(d.popitem(last=False))
''')

case("std_collections_defaultdict_list", "defaultdict(list) auto-creates lists", r'''
import collections

groups = collections.defaultdict(list)
groups["k"].append(1)
groups["k"].append(2)
print(groups["k"])
''')


# ---------------------------------------------------------------------------
# string / textwrap / shlex
# ---------------------------------------------------------------------------

case("std_string_template_substitute", "string.Template.substitute takes keywords", r'''
import string

print(string.Template("$greet, $name!").substitute(greet="Hi", name="Ada"))
''')

case("std_string_capwords_separator", "string.capwords accepts a separator", r'''
import string

print(string.capwords("a-b c", "-"))
''')

case("std_textwrap_dedent", "textwrap.dedent strips the common prefix", r'''
import textwrap

print(textwrap.dedent("    a\n    b\n"), end="")
''')

case("std_textwrap_wrap_width", "textwrap.wrap breaks at the given width", r'''
import textwrap

print(textwrap.wrap("aaa bbb ccc ddd", width=7))
''')

case("std_textwrap_shorten", "textwrap.shorten truncates with a placeholder", r'''
import textwrap

print(textwrap.shorten("one two three four", width=12))
''')

case("std_shlex_split_quotes", "shlex.split respects quoting", r'''
import shlex

print(shlex.split('a "b c" d'))
''')


# ---------------------------------------------------------------------------
# operator
# ---------------------------------------------------------------------------

case("std_operator_itemgetter_multi", "itemgetter with several indices returns a tuple", r'''
import operator

print(operator.itemgetter(2, 0)("abc"))
''')

case("std_operator_attrgetter", "attrgetter reads a named attribute", r'''
import operator


class Holder:
    def __init__(self, v):
        self.v = v


print(operator.attrgetter("v")(Holder(9)))
''')

case("std_operator_methodcaller_args", "methodcaller forwards its extra arguments", r'''
import operator

print(operator.methodcaller("replace", "a", "b")("banana"))
''')

case("std_operator_truth_is_bool", "operator.truth returns a real bool", r'''
import operator

print(operator.truth([]))
print(operator.truth([0]))
''')


# ---------------------------------------------------------------------------
# re -- optional count/maxsplit arguments and group access
# ---------------------------------------------------------------------------

case("std_re_sub_count", "re.sub accepts a count limit", r'''
import re

print(re.sub("a", "X", "aaa", count=2))
''')

case("std_re_split_maxsplit", "re.split accepts maxsplit", r'''
import re

print(re.split(r"\s+", "a b c", maxsplit=1))
''')

case("std_re_findall_groups", "findall returns tuples when there are groups", r'''
import re

print(re.findall(r"(\w)(\d)", "a1 b2"))
''')

case("std_re_named_group", "a named group is reachable by name", r'''
import re

m = re.match(r"(?P<user>\w+)@(?P<host>\w+)", "ada@host")
print(m.group("user"))
print(m.group("host"))
''')

case("std_re_ignorecase_flag", "re.IGNORECASE is honoured", r'''
import re

print(re.match("abc", "ABC", re.IGNORECASE) is not None)
print(re.match("abc", "ABC") is not None)
''')

case("std_re_match_span", "a match reports its span", r'''
import re

m = re.search(r"\d+", "ab123cd")
print(m.start())
print(m.end())
print(m.group(0))
''')


# ---------------------------------------------------------------------------
# binary / encoding modules
# ---------------------------------------------------------------------------

case("std_base64_roundtrip", "b64encode/b64decode round-trip", r'''
import base64

encoded = base64.b64encode(b"hello")
print(encoded)
print(base64.b64decode(encoded))
''')

case("std_base64_urlsafe", "urlsafe_b64encode uses the URL alphabet", r'''
import base64

print(base64.urlsafe_b64encode(b"\xfb\xff"))
''')

case("std_binascii_crc32", "binascii.crc32 matches CPython's checksum", r'''
import binascii

print(binascii.crc32(b"hello"))
''')

case("std_binascii_unhexlify", "unhexlify inverts hexlify", r'''
import binascii

print(binascii.unhexlify(binascii.hexlify(b"ab")))
''')

case("std_zlib_roundtrip", "zlib compress/decompress round-trips", r'''
import zlib

data = b"hello hello hello hello"
print(zlib.decompress(zlib.compress(data)) == data)
''')

case("std_struct_pack_unpack", "struct round-trips a packed record", r'''
import struct

packed = struct.pack("<ih", 7, 3)
print(len(packed))
print(struct.unpack("<ih", packed))
''')

case("std_struct_calcsize", "struct.calcsize reports the format width", r'''
import struct

print(struct.calcsize("<i"))
print(struct.calcsize("<q"))
''')


# ---------------------------------------------------------------------------
# modules the audit lists as having no bindings at all
# ---------------------------------------------------------------------------

case("std_csv_writer_stringio", "csv.writer writes rows to a text stream", r'''
import csv
import io

buf = io.StringIO()
writer = csv.writer(buf, lineterminator="\n")
writer.writerow(["a", "b"])
writer.writerow([1, 2])
print(buf.getvalue(), end="")
''')

case("std_reprlib_repr_truncates", "reprlib.repr abbreviates a long list", r'''
import reprlib

print(reprlib.repr(list(range(20))))
''')

case("std_unicodedata_name", "unicodedata.name resolves a character name", r'''
import unicodedata

print(unicodedata.name("A"))
''')

case("std_unicodedata_category", "unicodedata.category classifies a character", r'''
import unicodedata

print(unicodedata.category("1"))
print(unicodedata.category("a"))
''')

case("std_marshal_roundtrip", "marshal round-trips a simple structure", r'''
import marshal

print(marshal.loads(marshal.dumps([1, 2, 3])))
''')

case("std_uuid_from_hex", "uuid.UUID parses a hex string and exposes .int", r'''
import uuid

u = uuid.UUID("12345678-1234-5678-1234-567812345678")
print(str(u))
print(u.int)
''')


# ---------------------------------------------------------------------------
# remaining single-contract probes
# ---------------------------------------------------------------------------

case("std_types_simplenamespace_kwargs", "SimpleNamespace takes keyword fields", r'''
import types

ns = types.SimpleNamespace(a=1, b="two")
print(ns.a)
print(ns.b)
''')

case("std_heapq_nsmallest_key", "nsmallest accepts a key= function", r'''
import heapq

print(heapq.nsmallest(2, [(1, "b"), (0, "c"), (2, "a")], key=lambda p: p[1]))
''')

case("std_heapq_heappushpop", "heappushpop pushes then pops in one step", r'''
import heapq

heap = [1, 3, 5]
heapq.heapify(heap)
print(heapq.heappushpop(heap, 4))
print(sorted(heap))
''')

case("std_bisect_insort_keeps_order", "bisect.insort inserts in sorted position", r'''
import bisect

xs = [1, 3, 5]
bisect.insort(xs, 4)
print(xs)
''')

case("std_bisect_bisect_left_right", "bisect_left and bisect_right straddle a run", r'''
import bisect

xs = [1, 2, 2, 3]
print(bisect.bisect_left(xs, 2))
print(bisect.bisect_right(xs, 2))
''')

case("std_copy_deepcopy_is_independent", "deepcopy does not alias nested state", r'''
import copy

original = {"xs": [1, 2]}
clone = copy.deepcopy(original)
clone["xs"].append(3)
print(original["xs"])
print(clone["xs"])
''')

case("std_fractions_arithmetic", "Fraction keeps exact rational arithmetic", r'''
from fractions import Fraction

print(Fraction(1, 2) + Fraction(1, 3))
''')

case("std_decimal_exact_addition", "Decimal addition is exact where float is not", r'''
from decimal import Decimal

print(Decimal("0.1") + Decimal("0.2"))
print(Decimal("0.1") + Decimal("0.2") == Decimal("0.3"))
''')

case("std_array_typecode_roundtrip", "array stores and reads back typed elements", r'''
import array

a = array.array("i", [1, 2])
a.append(3)
print(a.typecode)
print(list(a))
''')

case("std_io_stringio_readlines", "StringIO replays written lines", r'''
import io

buf = io.StringIO("a\nb\n")
print(buf.readlines())
''')

case("std_ospath_splitext_returns_tuple", "os.path.splitext returns a tuple", r'''
import os.path

parts = os.path.splitext("file.tar.gz")
print(parts)
print(type(parts).__name__)
''')

case("std_ospath_basename_dirname", "basename/dirname split a posix-style path", r'''
import os.path

print(os.path.basename("a/b/c.txt"))
print(os.path.dirname("a/b/c.txt"))
''')

case("std_numbers_isinstance", "int/float register as numbers.Number", r'''
import numbers

print(isinstance(1, numbers.Number))
print(isinstance(1.5, numbers.Number))
print(isinstance("x", numbers.Number))
''')

case("std_enum_member_lookup", "an Enum member exposes name and value", r'''
import enum


class Color(enum.Enum):
    RED = 1
    GREEN = 2


print(Color.RED.name)
print(Color.RED.value)
print(Color(2).name)
''')

case("std_enum_intenum_arithmetic", "IntEnum members behave as ints", r'''
import enum


class Level(enum.IntEnum):
    LOW = 1
    HIGH = 5


print(Level.HIGH + 1)
print(Level.LOW < Level.HIGH)
''')

case("std_dataclass_field_default", "a dataclass applies declared defaults", r'''
import dataclasses


@dataclasses.dataclass
class Config:
    name: str
    retries: int = 3


c = Config("x")
print(c.name)
print(c.retries)
''')

case("std_contextlib_suppress", "contextlib.suppress swallows the named error", r'''
import contextlib

with contextlib.suppress(ValueError):
    raise ValueError("ignored")
print("continued")
''')


# ===========================================================================
# Wave 2. The first 116 probes came back 55% failing, so the area is nowhere
# near sampled out. This wave reaches the modules the first pass did not touch
# at all -- roughly 80 of the 117 files in asmpython/stdlib/ still have no
# probe of any kind.
#
# Everything here stays deterministic and machine-independent: no real
# filesystem, no clock, no network, no locale-sensitive output. Where a module
# normally needs a file (zipfile, gzip, configparser), the probe drives it
# through an in-memory stream instead, so the binding is still exercised.
# ===========================================================================

case("std_sys_version_major", "sys.version_info reports Python 3", r'''
import sys

print(sys.version_info[0])
print(sys.version_info.major)
''')

case("std_sys_maxsize", "sys.maxsize is the 64-bit signed maximum", r'''
import sys

print(sys.maxsize)
print(sys.maxsize == 2 ** 63 - 1)
''')

case("std_sys_byteorder", "sys.byteorder names the host endianness", r'''
import sys

print(sys.byteorder)
''')

case("std_ospath_split", "os.path.split separates the last component", r'''
import os.path

print(os.path.split("a/b/c.txt"))
''')

case("std_pathlib_pure_parts", "PurePosixPath decomposes into parts", r'''
import pathlib

p = pathlib.PurePosixPath("a/b/c.txt")
print(p.parts)
print(p.name)
print(p.suffix)
''')

case("std_pathlib_pure_joinpath", "PurePosixPath joins with /", r'''
import pathlib

print(str(pathlib.PurePosixPath("a") / "b" / "c.txt"))
''')

case("std_pathlib_pure_parent", "PurePosixPath exposes its parent", r'''
import pathlib

print(str(pathlib.PurePosixPath("a/b/c.txt").parent))
''')

case("std_urlparse_components", "urlparse splits a URL into components", r'''
from urllib.parse import urlparse

u = urlparse("https://example.com:8080/path?q=1#frag")
print(u.scheme)
print(u.hostname)
print(u.port)
print(u.path)
print(u.query)
print(u.fragment)
''')

case("std_urlencode_pairs", "urlencode builds a query string", r'''
from urllib.parse import urlencode

print(urlencode({"a": 1, "b": "x y"}))
''')

case("std_urlquote_escapes", "quote percent-escapes reserved characters", r'''
from urllib.parse import quote, unquote

encoded = quote("a b/c")
print(encoded)
print(unquote(encoded))
''')

case("std_html_escape", "html.escape replaces markup characters", r'''
import html

print(html.escape("<a href='x'>&</a>"))
''')

case("std_html_unescape", "html.unescape resolves entities", r'''
import html

print(html.unescape("&lt;tag&gt; &amp; &#65;"))
''')

case("std_calendar_weekday", "calendar.weekday is Monday-zero", r'''
import calendar

print(calendar.weekday(2020, 1, 6))
''')

case("std_cmath_sqrt_negative", "cmath.sqrt handles a negative real", r'''
import cmath

print(cmath.sqrt(-1))
''')

case("std_cmath_phase", "cmath.phase returns the argument angle", r'''
import cmath

print(cmath.phase(complex(0, 1)))
''')

case("std_colorsys_rgb_to_hsv", "colorsys converts RGB to HSV", r'''
import colorsys

print(colorsys.rgb_to_hsv(1.0, 0.0, 0.0))
''')

case("std_fnmatch_filter", "fnmatch.filter selects matching names", r'''
import fnmatch

print(fnmatch.filter(["a.py", "b.txt", "c.py"], "*.py"))
''')

case("std_difflib_ndiff", "difflib.ndiff marks per-line differences", r'''
import difflib

for line in difflib.ndiff(["a", "b"], ["a", "c"]):
    print(line)
''')

case("std_codecs_rot13", "codecs.encode applies a named codec", r'''
import codecs

print(codecs.encode("abc", "rot13"))
''')

# Deliberately does not PRINT the non-ASCII text: the runner captures stdout
# with text=True, i.e. the locale codec (cp1252 on this host), so a probe that
# printed it would be testing the harness's decoding as much as the compiler's.
# Printing the byte count and the round-trip verdict keeps the subject the
# codec itself.
case("std_codecs_utf8_roundtrip", "str.encode/bytes.decode round-trip UTF-8", r'''
text = "café"
encoded = text.encode("utf-8")
print(len(text))
print(len(encoded))
print(encoded.decode("utf-8") == text)
''')

case("std_stat_mode_predicates", "stat.S_ISDIR reads a mode word", r'''
import stat

print(stat.S_ISDIR(0o040755))
print(stat.S_ISREG(0o040755))
''')

case("std_errno_constant", "errno exposes the standard error numbers", r'''
import errno

print(errno.ENOENT)
print(errno.EEXIST)
''')

case("std_keyword_iskeyword", "keyword.iskeyword recognises reserved words", r'''
import keyword

print(keyword.iskeyword("class"))
print(keyword.iskeyword("banana"))
''')

case("std_pickle_roundtrip_list", "pickle round-trips a list", r'''
import pickle

print(pickle.loads(pickle.dumps([1, "two", 3.0])))
''')

case("std_pickle_roundtrip_dict", "pickle round-trips a dict", r'''
import pickle

original = {"a": 1, "b": [2, 3]}
print(pickle.loads(pickle.dumps(original)) == original)
''')

case("std_pprint_pformat_sorts", "pprint.pformat sorts dict keys", r'''
import pprint

print(pprint.pformat({"b": 1, "a": 2}))
''')

case("std_traceback_format_exception_only", "traceback renders an exception line", r'''
import traceback

print(traceback.format_exception_only(ValueError, ValueError("boom"))[0], end="")
''')

case("std_warnings_catch", "warnings.catch_warnings records a warning", r'''
import warnings

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    warnings.warn("careful", UserWarning)
print(len(caught))
print(str(caught[0].message))
''')

case("std_logmod_writes_to_stream", "the logging module emits through a handler", r'''
import io
import logging

buf = io.StringIO()
handler = logging.StreamHandler(buf)
handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
logger = logging.getLogger("probe")
logger.setLevel(logging.INFO)
logger.addHandler(handler)
logger.info("hello")
logger.debug("hidden")
print(buf.getvalue(), end="")
''')

case("std_logmod_level_filters", "a logger drops records below its level", r'''
import io
import logging

buf = io.StringIO()
handler = logging.StreamHandler(buf)
handler.setFormatter(logging.Formatter("%(message)s"))
logger = logging.getLogger("levels")
logger.setLevel(logging.WARNING)
logger.addHandler(handler)
logger.warning("kept")
logger.info("dropped")
print(buf.getvalue(), end="")
''')

case("std_queue_fifo_order", "queue.Queue is first-in first-out", r'''
import queue

q = queue.Queue()
q.put("a")
q.put("b")
print(q.get())
print(q.get())
print(q.empty())
''')

case("std_queue_lifo", "queue.LifoQueue reverses the order", r'''
import queue

q = queue.LifoQueue()
q.put("a")
q.put("b")
print(q.get())
''')

case("std_threading_join_completes", "a joined thread has finished its work", r'''
import threading

result = []


def work():
    result.append("done")


t = threading.Thread(target=work)
t.start()
t.join()
print(result)
print(t.is_alive())
''')

case("std_threading_lock_guards", "a Lock works as a context manager", r'''
import threading

lock = threading.Lock()
with lock:
    print("inside")
print(lock.locked())
''')

case("std_atexit_runs_handler", "an atexit handler runs at interpreter exit", r'''
import atexit


def farewell():
    print("atexit ran")


atexit.register(farewell)
print("main done")
''')

case("std_weakref_resolves_while_alive", "a weakref resolves while the target lives", r'''
import weakref


class Target:
    pass


t = Target()
ref = weakref.ref(t)
print(ref() is t)
''')

case("std_inspect_signature", "inspect.signature renders a parameter list", r'''
import inspect


def sample(a, b=1, *rest, key=None):
    return a


print(str(inspect.signature(sample)))
''')

case("std_typing_get_type_hints", "get_type_hints resolves annotations", r'''
import typing


def annotated(a: int, b: str) -> bool:
    return True


hints = typing.get_type_hints(annotated)
print(hints["a"].__name__)
print(hints["b"].__name__)
print(hints["return"].__name__)
''')

case("std_typing_optional_args", "typing.Optional exposes its arguments", r'''
import typing

print(typing.get_args(typing.Optional[int]))
''')

case("std_ipaddress_address", "IPv4Address parses and compares", r'''
import ipaddress

a = ipaddress.IPv4Address("10.0.0.1")
print(str(a))
print(int(a))
print(a < ipaddress.IPv4Address("10.0.0.2"))
''')

case("std_ipaddress_network_contains", "an IPv4Network contains its members", r'''
import ipaddress

net = ipaddress.IPv4Network("10.0.0.0/30")
print(net.num_addresses)
print(ipaddress.IPv4Address("10.0.0.1") in net)
print(ipaddress.IPv4Address("10.0.1.1") in net)
''')

case("std_configparser_read_string", "configparser parses an INI document", r'''
import configparser

parser = configparser.ConfigParser()
parser.read_string("[main]\nname = ada\ncount = 2\n")
print(parser["main"]["name"])
print(parser.getint("main", "count"))
''')

case("std_argparse_parses_list", "argparse parses an explicit argument list", r'''
import argparse

parser = argparse.ArgumentParser(prog="probe")
parser.add_argument("--count", type=int, default=1)
parser.add_argument("name")
args = parser.parse_args(["--count", "3", "ada"])
print(args.name)
print(args.count)
''')

case("std_getopt_short_options", "getopt parses short options", r'''
import getopt

opts, rest = getopt.getopt(["-a", "1", "extra"], "a:")
print(opts)
print(rest)
''')

case("std_xml_etree_fromstring", "ElementTree parses a document", r'''
import xml.etree.ElementTree as ET

root = ET.fromstring("<root><item name='a'>1</item><item name='b'>2</item></root>")
print(root.tag)
print([item.get("name") for item in root])
print([item.text for item in root])
''')

case("std_zipfile_in_memory", "zipfile writes and reads a member in memory", r'''
import io
import zipfile

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as archive:
    archive.writestr("hello.txt", "contents")
with zipfile.ZipFile(buf) as archive:
    print(archive.namelist())
    print(archive.read("hello.txt").decode("utf-8"))
''')

case("std_gzip_in_memory", "gzip round-trips through a byte stream", r'''
import gzip

data = b"repeat repeat repeat"
print(gzip.decompress(gzip.compress(data)) == data)
''')

case("std_bz2_roundtrip", "bz2 compress/decompress round-trips", r'''
import bz2

data = b"repeat repeat repeat"
print(bz2.decompress(bz2.compress(data)) == data)
''')

case("std_secrets_token_length", "secrets.token_hex returns the requested width", r'''
import secrets

print(len(secrets.token_hex(8)))
''')

case("std_string_constants", "string exposes the ASCII constants", r'''
import string

print(string.ascii_lowercase)
print(string.digits)
print(string.hexdigits)
''')

case("std_datetime_isoformat_parse", "date.fromisoformat inverts isoformat", r'''
import datetime

print(datetime.date.fromisoformat("2020-02-29").isoformat())
''')

case("std_decimal_quantize", "Decimal.quantize rounds to a fixed exponent", r'''
from decimal import Decimal

print(Decimal("2.345").quantize(Decimal("0.01")))
''')

case("std_fractions_limit_denominator", "Fraction.limit_denominator approximates", r'''
from fractions import Fraction

print(Fraction(3141592, 1000000).limit_denominator(100))
''')

case("std_enum_iteration_order", "an Enum iterates in declaration order", r'''
import enum


class Color(enum.Enum):
    RED = 1
    GREEN = 2
    BLUE = 3


print([member.name for member in Color])
''')

case("std_dataclass_repr", "a dataclass gets a generated repr", r'''
import dataclasses


@dataclasses.dataclass
class Point:
    x: int
    y: int


print(repr(Point(1, 2)))
''')

case("std_dataclass_eq", "a dataclass compares by field values", r'''
import dataclasses


@dataclasses.dataclass
class Point:
    x: int
    y: int


print(Point(1, 2) == Point(1, 2))
print(Point(1, 2) == Point(2, 1))
''')

case("std_contextlib_exitstack", "ExitStack unwinds in reverse order", r'''
import contextlib


@contextlib.contextmanager
def named(name):
    yield name
    print("closed " + name)


with contextlib.ExitStack() as stack:
    stack.enter_context(named("a"))
    stack.enter_context(named("b"))
    print("body")
''')

# Uses enterabs with a FROZEN time function. The obvious version --
# `enter(0, priority, ...)` twice -- looks deterministic and is not: enter()
# resolves the delay against monotonic() at call time, so the two events get
# different absolute times and the earlier CALL wins regardless of priority.
# It happened to print the same order on both generation runs, which is
# exactly how a timing-dependent expectation gets into a corpus.
case("std_sched_runs_in_time_order", "sched runs due events in timestamp order", r'''
import sched

scheduler = sched.scheduler(lambda: 100.0, lambda _: None)
scheduler.enterabs(2.0, 1, print, ("second",))
scheduler.enterabs(1.0, 1, print, ("first",))
scheduler.run()
''')


if __name__ == "__main__":
    raise SystemExit(main(CASES, "gen_std_cases.py", sys.argv))
