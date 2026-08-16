# probes: unicodedata.category classifies a character
# expect:
# Nd
# Ll
import unicodedata

print(unicodedata.category("1"))
print(unicodedata.category("a"))
