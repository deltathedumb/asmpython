# tier: spec
# ref: library/functions.html#zip
# expect:
# [(1, 'a'), (2, 'b')]
# [(1, 'a'), (2, 'b')]
# ValueError
# [(1, 'a'), (2, 'b')]
print(list(zip([1, 2], "ab")))
print(list(zip([1, 2, 3], "ab")))
try:
    list(zip([1, 2, 3], "ab", strict=True))
except ValueError:
    print("ValueError")
print(list(zip([1, 2], "ab", strict=True)))
