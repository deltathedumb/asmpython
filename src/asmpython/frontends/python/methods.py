"""What `obj.method(...)` lowers to.

Its own module because it is a TABLE, not logic, and because both analysis and
lowering read it -- analysis to decide whether a call has a shape it can
accept, lowering to pick the symbol. Two copies of it would drift, and the way
they would drift is a method that type-checks and then fails to link.

There is no bound-method value here: `xs.append` on its own is not something
this can produce, only `xs.append(v)` as a whole. Method lookup on an instance
will need that to change.
"""
from __future__ import annotations


#: `obj.method(...)` -> the runtime symbol, indexed by ARGUMENT COUNT.
#:
#: A list per method, position `k` holding the symbol for `k` arguments and
#: `None` where that arity does not exist. Written out rather than derived from
#: the symbol names, even though the runtime's naming is regular
#: (`apy_str_find` / `find2` / `find3`), because the irregular ones are exactly
#: the ones worth being explicit about: `split()` with no argument splits on
#: RUNS of whitespace and `split(' ')` does not, so they are different
#: functions, not the same function with a default.
#:
#: A method missing from here is reported by analysis with its name. A method
#: present here whose symbol the runtime does not define is a link error, which
#: is why `_check_methods_exist` asserts the two agree at import.
DYN_METHOD_TABLE = {
    # sequences
    "append":       [None, "apy_seq_push"],
    "add":          [None, "apy_set_add"],
    "discard":      [None, "apy_set_discard"],
    "index":        [None, "apy_index_of"],
    # `count` on a LIST takes only the item; the start/end forms are str's
    # alone, which is why the wider arities name the string entry points
    # directly. A list reaching them is not a shape Python allows.
    "count":        [None, "apy_count_of", "apy_str_count2",
                     "apy_str_count3"],
    "remove":       [None, "apy_list_remove"],
    "insert":       [None, None, "apy_list_insert"],
    "extend":       [None, "apy_extend"],
    "reverse":      ["apy_list_reverse"],
    "clear":        ["apy_clear"],
    "copy":         ["apy_copy"],
    # `sort` is IN PLACE and answers None; `sorted` is the other one. Both
    # arities take the key and reverse VALUES, which lowering supplies as
    # None/False when the call omitted them -- see `_dyn_builtin_method`.
    "sort":         ["apy_list_sort"],
    # dict. All three are `apy_dict_parts` with a selector, so the table
    # records the shape and lowering supplies the constant -- see
    # `DICT_PARTS`.
    "keys":         ["apy_dict_parts"],
    "values":       ["apy_dict_parts"],
    "items":        ["apy_dict_parts"],
    "get":          [None, "apy_dict_get_or", "apy_dict_get_or"],
    "setdefault":   [None, "apy_setdefault", "apy_setdefault"],
    # `d.update()` with no argument is a no-op, and `d.update(k=v)` is
    # all-keywords -- so the zero-argument shape is real. See
    # `_dyn_builtin_method`, which supplies the keyword dict.
    "update":       ["apy_update", "apy_update"],
    # `pop` reaches a LIST, a SET or a DICT, and the receiver is not known
    # until run time -- so the one-argument form dispatches inside
    # `apy_list_pop`, and the two-argument form is a dict's alone.
    "pop":          ["apy_list_pop", "apy_list_pop", "apy_pop_or"],
    "popitem":      ["apy_dict_popitem"],
    # set algebra, as methods
    "union":            [None, "apy_set_union"],
    "intersection":     [None, "apy_set_intersection"],
    "difference":       [None, "apy_set_difference"],
    "symmetric_difference": [None, "apy_set_symdiff"],
    "issubset":     [None, "apy_set_issubset"],
    "issuperset":   [None, "apy_set_issuperset"],
    "isdisjoint":   [None, "apy_set_isdisjoint"],
    # str: case and shape
    "upper":        ["apy_str_upper"],
    "lower":        ["apy_str_lower"],
    "title":        ["apy_str_title"],
    "capitalize":   ["apy_str_capitalize"],
    "swapcase":     ["apy_str_swapcase"],
    "casefold":     ["apy_str_casefold"],
    "zfill":        [None, "apy_str_zfill"],
    "center":       [None, "apy_str_center", "apy_str_center_fill"],
    "ljust":        [None, "apy_str_ljust", "apy_str_ljust_fill"],
    "rjust":        [None, "apy_str_rjust", "apy_str_rjust_fill"],
    # str: trimming. The no-argument form strips WHITESPACE; the one-argument
    # form strips any character in the set, which is not the same operation.
    "strip":        ["apy_str_strip", "apy_str_strip_chars"],
    "lstrip":       ["apy_str_lstrip", "apy_str_lstrip_chars"],
    "rstrip":       ["apy_str_rstrip", "apy_str_rstrip_chars"],
    "removeprefix": [None, "apy_str_removeprefix"],
    "removesuffix": [None, "apy_str_removesuffix"],
    # str: splitting and joining
    "split":        ["apy_str_split_ws", "apy_split_of", "apy_str_split_n"],
    "rsplit":       ["apy_str_rsplit_ws", "apy_str_rsplit", "apy_str_rsplit_n"],
    "splitlines":   ["apy_str_splitlines", "apy_str_splitlines_keep"],
    "partition":    [None, "apy_str_partition"],
    "rpartition":   [None, "apy_str_rpartition"],
    "join":         [None, "apy_str_join"],
    "replace":      [None, None, "apy_str_replace", "apy_str_replace_n"],
    # str: searching
    "find":         [None, "apy_str_find", "apy_str_find2", "apy_str_find3"],
    "rfind":        [None, "apy_str_rfind", "apy_str_rfind2", "apy_str_rfind3"],
    "rindex":       [None, "apy_str_rindex"],
    "startswith":   [None, "apy_str_startswith", "apy_str_startswith2",
                     "apy_str_startswith3"],
    "endswith":     [None, "apy_str_endswith", "apy_str_endswith2",
                     "apy_str_endswith3"],
    # str: classification
    "isalpha":      ["apy_str_isalpha"],
    "isdigit":      ["apy_str_isdigit"],
    "isdecimal":    ["apy_str_isdecimal"],
    "isnumeric":    ["apy_str_isnumeric"],
    "isalnum":      ["apy_str_isalnum"],
    "isspace":      ["apy_str_isspace"],
    "islower":      ["apy_str_islower"],
    "isupper":      ["apy_str_isupper"],
    "istitle":      ["apy_str_istitle"],
    "isascii":      ["apy_str_isascii"],
    "isprintable":  ["apy_str_isprintable"],
    "isidentifier": ["apy_str_isidentifier"],
    # str <-> bytes, and the numeric methods
    # The ENCODING ARGUMENT is accepted and ignored: this runtime stores text
    # as UTF-8 already, so encoding is a change of kind and not of content.
    # That makes both exact for UTF-8 and wrong for every other codec -- and
    # refusing the argument would reject the spelling nearly all code uses.
    # THE ERROR HANDLER IS THE SECOND ARGUMENT, and every arity reaches the
    # same three-parameter entry -- lowering pads the ones the call omitted,
    # because `errors="replace"` is what decides whether a bad byte is a
    # refusal or a replacement character.
    "encode":       ["apy_str_encode", "apy_str_encode", "apy_str_encode"],
    "decode":       ["apy_bytes_decode", "apy_bytes_decode",
                     "apy_bytes_decode"],
    "bit_length":   ["apy_bit_length"],
    "bit_count":    ["apy_bit_count"],
    "is_integer":   ["apy_is_integer"],
    "conjugate":    ["apy_conjugate"],
    # `hex` with a separator, `to_bytes` and `expandtabs` all take an argument
    # the no-argument form supplies a default for -- see `_dyn_builtin_method`.
    # `hex` reaches BYTES or a FLOAT, and the two answer entirely different
    # things -- so the no-argument form dispatches on the receiver. The
    # separator form is bytes' alone.
    "hex":          ["apy_hex_of", "apy_bytes_hex"],
    "fromhex":      [None, "apy_bytes_fromhex"],
    "to_bytes":     [None, None, "apy_to_bytes_n"],
    "as_integer_ratio": ["apy_as_integer_ratio"],
    "expandtabs":   ["apy_str_expandtabs", "apy_str_expandtabs"],
    "translate":    [None, "apy_str_translate"],
    # DUNDERS CALLED DIRECTLY ON A BUILTIN. `(-5).__abs__()` and
    # `(0.0).__bool__()` are ordinary Python, and every one of these is the
    # operation the runtime already performs for the operator form -- so they
    # are the same symbol reached by another spelling, not a second
    # implementation that could disagree with it.
    "__abs__":      ["apy_abs"],
    "__bool__":     ["apy_to_bool"],
    "__trunc__":    ["apy_math_trunc"],
    "__neg__":      ["apy_neg"],
    "__len__":      ["apy_len"],
    "__repr__":     ["apy_repr"],
    "__str__":      ["apy_str"],
    "add_note":     [None, "apy_add_note"],
    # PEP 654. `subgroup` answers one group or None; `split` is shared with
    # `str.split` and dispatches on the receiver -- see `apy_split_of`.
    "subgroup":     [None, "apy_group_subgroup"],
    # `s.indices(n)` -- the numbers a walk over a sequence of that length
    # would really use, with omitted and negative bounds resolved.
    "indices":      [None, "apy_slice_indices"],
    # `@v.setter` and `@v.getter` -- each answers a NEW property carrying the
    # other half, because the decorator's result is rebound afterwards.
    "setter":       [None, "apy_prop_setter"],
    "getter":       [None, "apy_prop_getter"],
    # generators
    "send":         [None, "apy_gen_send"],
    "throw":        [None, "apy_gen_throw"],
    "close":        ["apy_gen_close"],
}

#: `index` and `count` are on BOTH a sequence and a str, and the runtime has a
#: different symbol for each. The receiver's kind is not known at compile time,
#: so the sequence one is emitted and the runtime falls back to the string
#: behaviour when handed a str -- which is why `apy_index_of`/`apy_count_of`
#: accept one.
_SHAPES = frozenset(
    (name, k) for name, syms in DYN_METHOD_TABLE.items()
    for k, sym in enumerate(syms) if sym is not None
)


#: Which of a dict's three parts each method wants, matching the runtime's
#: `apy_dict_parts` selector.
DICT_PARTS = {"keys": 0, "values": 1, "items": 2}


def method_symbol(name: str, argc: int) -> str | None:
    """The runtime symbol for `name` called with `argc` arguments, or None."""
    syms = DYN_METHOD_TABLE.get(name)
    if syms is None or argc >= len(syms):
        return None
    return syms[argc]
