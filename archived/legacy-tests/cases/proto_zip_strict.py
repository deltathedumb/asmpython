# probes: zip(strict=True) rejects ragged inputs (3.10+)
# expect:
# ragged refused
# [(1, 'a'), (2, 'b')]
try:
    print(list(zip([1, 2], ["a"], strict=True)))
except ValueError:
    print("ragged refused")
print(list(zip([1, 2], ["a", "b"], strict=True)))
