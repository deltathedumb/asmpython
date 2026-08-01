# tier: spec
# ref: library/unicodedata.html
# expect:
# 1 2
# False
# True
# True
# Ll
import unicodedata

composed = "\u00e9"
decomposed = "e\u0301"
print(len(composed), len(decomposed))
print(composed == decomposed)
print(unicodedata.normalize("NFC", decomposed) == composed)
print(unicodedata.normalize("NFD", composed) == decomposed)
print(unicodedata.category(composed))
