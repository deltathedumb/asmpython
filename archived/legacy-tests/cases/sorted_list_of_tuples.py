# expect:
# [(1, 'a'), (2, 'b')]
print(sorted([(2, "b"), (1, "a")]))
# asmpython (beta/3.14.0): runtime segfault (exit 0xC0000005) sorting a list
# of (int, str) tuples.
