# COVERAGE: dumps/loads/dump/load with every keyword each documents --
# skipkeys, ensure_ascii, check_circular, allow_nan, indent, separators,
# default, sort_keys on the encoding side; parse_float, parse_int,
# parse_constant, object_hook, object_pairs_hook on the decoding side.
# JSONEncoder and JSONDecoder used directly. JSONDecodeError's .msg/.pos/
# .lineno/.colno for several concrete malformed documents. Round-tripping
# nested objects/arrays, string escapes (including \uXXXX and a surrogate
# pair), negative/exponent/float numbers, true/false/null. NOT covered: the
# cls= parameter (not in this module's signature, so passing it is an
# ordinary TypeError), and bytes/bytearray input beyond plain UTF-8.
import json

# ---- round trip: nested objects/arrays, escapes, unicode, numbers ---------
doc = {
    "name": "uasm",
    "nested": {"a": [1, 2, 3], "b": {"c": None, "d": [True, False]}},
    "list_of_dicts": [{"x": 1}, {"x": 2}, {"x": 3}],
    "escapes": "quote\"backslash\\slash/tab\tnewline\ncr\rbs\bff\f",
    "unicode_bmp": "café ☃ snowman",
    "unicode_astral": "\U0001F600\U0002F800",
    "numbers": {
        "int": 42,
        "neg_int": -17,
        "big_int": 123456789012345678901234567890,
        "zero": 0,
        "neg_zero": -0.0,
        "float": 3.14159,
        "neg_float": -2.5,
        "exp": 1e10,
        "neg_exp": -1.5e-10,
        "small": 5e-324,
        "one": 1.0,
    },
    "bools": [True, False],
    "null": None,
    "empty_list": [],
    "empty_dict": {},
}
encoded = json.dumps(doc)
decoded = json.loads(encoded)
print(decoded == doc)
print(decoded["escapes"])
print(decoded["unicode_bmp"])
print(decoded["unicode_astral"])
print(type(decoded["numbers"]["int"]).__name__,
      type(decoded["numbers"]["float"]).__name__,
      type(decoded["numbers"]["one"]).__name__)
print(decoded["numbers"]["big_int"])
print(decoded["numbers"]["exp"], decoded["numbers"]["neg_exp"])

# \uXXXX bare escapes and a surrogate pair spelling an astral character.
print(json.loads('"\\u0041\\u00e9\\u2603"'))
print(json.loads('"\\ud83d\\ude00"') == "\U0001F600")

# int vs float distinction on the boundary CPython draws it at.
print(type(json.loads("1")).__name__, type(json.loads("1.0")).__name__,
      type(json.loads("1e5")).__name__, type(json.loads("-3")).__name__)

# NaN/Infinity/-Infinity: outside the JSON spec, inside CPython's json.
special = json.loads("[NaN, Infinity, -Infinity]")
print(special[0] != special[0], special[1] > 0, special[2] < 0)
print(json.dumps(float("nan")), json.dumps(float("inf")), json.dumps(float("-inf")))

# ---- indent=2 formatting, exactly ------------------------------------------
print(json.dumps({"a": 1, "b": [1, 2, {"c": 3}]}, indent=2))
print(json.dumps([1, 2, 3], indent=2))
print(json.dumps({}, indent=2))
print(json.dumps([], indent=2))
print(json.dumps({"a": []}, indent=2))
print(json.dumps({"only": "one"}, indent=4))
print(json.dumps({"a": 1}, indent="--"))

# ---- sort_keys=True ---------------------------------------------------------
unsorted = {"z": 1, "a": 2, "m": 3, "b": 4}
print(json.dumps(unsorted, sort_keys=True))
print(json.dumps(unsorted, sort_keys=True, indent=2))

# ---- separators --------------------------------------------------------------
print(json.dumps({"a": 1, "b": [1, 2]}, separators=(",", ":")))

# ---- ensure_ascii=False with non-ASCII content ------------------------------
nonascii = {"city": "København", "smile": "\U0001F600", "cn": "中文"}
print(json.dumps(nonascii, ensure_ascii=True))
print(json.dumps(nonascii, ensure_ascii=False))
print(json.loads(json.dumps(nonascii, ensure_ascii=False)) == nonascii)

# ---- malformed JSON: JSONDecodeError with exact .pos/.lineno/.colno --------
bad_cases = [
    '{"a":}',
    '{"a" "b"}',
    '[1, 2',
    '{"a": 1,}',
    'nul',
    '"unterminated',
    '{\n  "a": 1,\n  "b":\n}',
]
for case in bad_cases:
    try:
        json.loads(case)
        print("NO ERROR:", repr(case))
    except json.JSONDecodeError as e:
        print(e.msg, e.pos, e.lineno, e.colno)
        print(str(e))

# ---- default= for a custom object -------------------------------------------
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y


def point_default(o):
    if isinstance(o, Point):
        return {"__point__": True, "x": o.x, "y": o.y}
    raise TypeError("not serializable: %r" % (o,))


p = Point(3, 4)
p_json = json.dumps({"origin": Point(0, 0), "point": p}, default=point_default,
                     sort_keys=True)
print(p_json)

try:
    json.dumps(Point(1, 1))
except TypeError as e:
    print("TypeError:", str(e))

# ---- object_hook= reconstructing an object on load --------------------------
def point_hook(d):
    if d.get("__point__"):
        return Point(d["x"], d["y"])
    return d


rebuilt = json.loads(p_json, object_hook=point_hook)
print(isinstance(rebuilt["origin"], Point), isinstance(rebuilt["point"], Point))
print(rebuilt["origin"].x, rebuilt["origin"].y, rebuilt["point"].x, rebuilt["point"].y)

# ---- object_pairs_hook -------------------------------------------------------
pairs = json.loads('{"a":1,"b":2,"a":3}', object_pairs_hook=lambda p: p)
print(pairs)

# ---- parse_float / parse_int / parse_constant -------------------------------
print(json.loads("[1, 2, 3]", parse_int=lambda s: int(s) * 100))
print(json.loads("[1.5, 2.5]", parse_float=lambda s: "F(" + s + ")"))
print(json.loads("[NaN]", parse_constant=lambda s: "CONST:" + s))

# ---- skipkeys / check_circular / allow_nan ----------------------------------
# Non-str KEYS are stringified in the JSON-defined order of scalar kinds --
# not sorted, since None/float/bool/str are not mutually comparable and
# `sort_keys` sorting by the ORIGINAL key would raise for exactly this dict.
print(json.dumps({1: "int-key", 2.5: "float-key", True: "bool-key",
                   None: "none-key", "s": "str-key"}))
try:
    json.dumps({(1, 2): "tuple-key"})
except TypeError as e:
    print("TypeError:", str(e))
print(json.dumps({(1, 2): "tuple-key", "ok": 1}, skipkeys=True))

try:
    json.dumps(float("nan"), allow_nan=False)
except ValueError as e:
    print("ValueError:", str(e))

circular = []
circular.append(circular)
try:
    json.dumps(circular)
except ValueError as e:
    print("ValueError:", str(e))

# ---- JSONEncoder / JSONDecoder used directly --------------------------------
enc = json.JSONEncoder(indent=2, sort_keys=True)
print(enc.encode({"b": 2, "a": 1}))

dec = json.JSONDecoder()
print(dec.decode('{"x": [1, 2, 3]}'))

# ---- dump/load against a fake file object (only .write/.read needed) -------
class FakeFile:
    def __init__(self, text=""):
        self._chunks = []
        self._text = text

    def write(self, chunk):
        self._chunks.append(chunk)

    def getvalue(self):
        return "".join(self._chunks)

    def read(self):
        return self._text


out = FakeFile()
json.dump({"a": 1, "b": [1, 2, 3]}, out, sort_keys=True)
print(out.getvalue())

inp = FakeFile(text='{"a": 1, "b": [1, 2, 3]}')
print(json.load(inp))

# tuples serialize as arrays, same as lists.
print(json.dumps((1, 2, 3)))
print(json.dumps({"t": (1, "two", 3.0)}))
