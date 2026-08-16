# expect:
# [1, 'two']
xs = [1, "two"]
print(xs)
# CPython allows heterogeneous lists; asmpython (beta/3.14.0) rejects with
# "[E051] mixed list element types". Flipped from cases_fail/list_mixed_types.py
# -- real Python behaviour must be a blocker toward 100%, not an intentional
# rejection.
