# tier: spec
# ref: peps.python.org/pep-0618/
# expect:
# [(1, 'a'), (2, 'b')]
# [(1, 'a'), (2, 'b')]
# ValueError
print(list(zip([1, 2], 'ab')))
print(list(zip([1, 2, 3], 'ab')))
try:
    list(zip([1, 2, 3], 'ab', strict=True))
except ValueError as e:
    print(type(e).__name__)
