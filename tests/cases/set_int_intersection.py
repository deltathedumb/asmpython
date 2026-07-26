# expect:
# [2, 3]
print(sorted({1, 2, 3} & {2, 3, 4}))
# asmpython (beta/3.14.0) prints ['2', '3']: int set elements are stored/read
# as strings, so the intersection result carries str-typed members.
