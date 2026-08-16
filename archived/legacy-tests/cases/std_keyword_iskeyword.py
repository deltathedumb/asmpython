# probes: keyword.iskeyword recognises reserved words
# expect:
# True
# False
import keyword

print(keyword.iskeyword("class"))
print(keyword.iskeyword("banana"))
