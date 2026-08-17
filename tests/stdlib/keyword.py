# COVERAGE: kwlist, softkwlist, iskeyword, issoftkeyword -- the whole module.
#
# Run under CPython and under asmpython; the outputs must be identical. So the
# assertions below are written against what the module IS SPECIFIED to do, not
# against what asmpython currently does -- a test that prints asmpython's
# answer and calls it correct tests nothing.
import keyword

print(len(keyword.kwlist))
print(keyword.kwlist[0], keyword.kwlist[-1])
print(keyword.kwlist == sorted(keyword.kwlist))

# THE ORDER IS OBSERVABLE, so it is compared element by element rather than as
# a set. CPython's list is sorted with the capitalised singletons first, which
# a naive alphabetical sort gets wrong.
print(keyword.kwlist[:5])
print(keyword.softkwlist)

for word in ["if", "else", "lambda", "nonlocal", "await", "async", "yield"]:
    print(word, keyword.iskeyword(word))

# NOT KEYWORDS, and each is a different reason: an ordinary name, a builtin, a
# soft keyword, and a word that only looks like one.
for word in ["spam", "print", "match", "case", "type", "_", "Match", ""]:
    print(word, keyword.iskeyword(word))

for word in ["match", "case", "type", "_", "if", "spam"]:
    print(word, keyword.issoftkeyword(word))

# THE TWO LISTS ARE DISJOINT. A soft keyword is a name everywhere the grammar
# is not expecting its construct, so nothing may be in both.
both = [w for w in keyword.softkwlist if keyword.iskeyword(w)]
print("in both:", both)

# And every hard keyword answers True, with no exceptions.
print("all hard:", all(keyword.iskeyword(w) for w in keyword.kwlist))
print("no soft is hard:", not any(keyword.iskeyword(w)
                                  for w in keyword.softkwlist))
