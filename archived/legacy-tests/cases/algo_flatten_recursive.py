# expect:
# [1, 2, 3, 4, 5, 6]
def flatten(lst):
    result = []
    for item in lst:
        if isinstance(item, list):
            result.extend(flatten(item))
        else:
            result.append(item)
    return result
print(flatten([1, [2, [3, 4], 5], 6]))
# asmpython (beta/3.14.0) MISMATCH: prints '[1, 9278352, 6]\n' (wrong).
