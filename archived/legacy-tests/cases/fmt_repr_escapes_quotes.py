# probes: repr picks a quoting style and escapes
# expect:
# "it's"
# 'say "hi"'
# 'back\\slash'
print(repr("it's"))
print(repr('say "hi"'))
print(repr("back\\slash"))
