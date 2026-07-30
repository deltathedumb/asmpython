# expect:
# 3
# ABC
# a
# abc
# 3
# 6
# 4.0
# 2
# ba
# 5
# 3

# An UNANNOTATED parameter takes its type from the call sites, when every
# literal argument agrees. That inference already existed but never reached
# these shapes, because `_mark_dynamic_parameters` ran FIRST and stamped
# ("any", None) onto any parameter it considered dynamically used -- including
# one merely ITERATED BY A FOR LOOP. The mark then defeated inference twice
# over: `_infer_call_target_params` skips a parameter whose annotation
# resolves, and the propagation refuses to overwrite one.
#
# `any` is not free for an iterable: the For lowering walks it with the LIST
# layout, so a str argument had its bytes read as element words. Before the
# ordering fix these SEGFAULTED rather than printing:
#
#     def f(s):
#         for ch in s: ...        -> exit 139
#     def f(s): return s[0]       -> exit 139
#     def f(s): return s.upper()  -> printed 0, not ABC
#
# while `len(s)` happened to work, a length read being valid on either header.
#
# Covers str and list parameters through the four routes that resolve them
# differently -- len, method call, subscript, iteration -- plus float, whose
# calling convention differs (XMM vs GP) and which was the only kind propagated
# before.
def take_len(s):
    return len(s)


def take_method(s):
    return s.upper()


def take_index(s):
    return s[0]


def take_iter_str(s):
    out = ""
    for ch in s:
        out = out + ch
    return out


def take_iter_list(xs):
    n = 0
    for x in xs:
        n = n + 1
    return n


def take_sum(xs):
    total = 0
    for x in xs:
        total = total + x
    return total


def take_float(a, b):
    return a + b


def take_len_list(xs):
    return len(xs)


def take_reverse(s):
    out = ""
    for ch in s:
        out = ch + out
    return out


# set / tuple are in the propagated-kind list too, so they are exercised here
# rather than trusted: every kind written through to the IR parameter type
# should have a case that would notice if it stopped working.
#
# "dict" is deliberately NOT in that list -- writing ("dict", None) forces an
# int value type and loses the dynamic handling `any` gave it, which cost
# app_validate_form.py an entry. See _INFERRED_PARAM_KINDS.
def take_tuple_index(t):
    return t[0]


def take_set_iter(s):
    n = 0
    for e in s:
        n = n + 1
    return n


print(take_len("abc"))
print(take_method("abc"))
print(take_index("abc"))
print(take_iter_str("abc"))
print(take_iter_list([1, 2, 3]))
print(take_sum([1, 2, 3]))
print(take_float(1.5, 2.5))
print(take_len_list([7, 8]))
print(take_reverse("ab"))
print(take_tuple_index((5, 6)))
print(take_set_iter({1, 2, 3}))
