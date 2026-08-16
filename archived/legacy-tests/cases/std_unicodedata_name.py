# probes: unicodedata.name resolves a character name
# expect:
# LATIN CAPITAL LETTER A
import unicodedata

print(unicodedata.name("A"))
