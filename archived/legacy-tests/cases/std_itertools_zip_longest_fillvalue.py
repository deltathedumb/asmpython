# probes: zip_longest accepts fillvalue=
# expect:
# [(1, 'a'), (2, '-'), (3, '-')]
import itertools

print(list(itertools.zip_longest([1, 2, 3], ["a"], fillvalue="-")))
