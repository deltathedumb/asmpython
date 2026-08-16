# expect:
# {1: 'a', 2: 'b'}
d: dict[str, int] = {"a": 1, "b": 2}
inv = {v: k for k, v in d.items()}
print(inv)
# CPython builds a dict with int keys; asmpython (beta/3.14.0) rejects with
# "[E054] dict comprehension keys must be strings". Flipped from
# cases_fail/dict_comp_int_key.py -- real Python behaviour is a 100% blocker.
