# probes: min/max accept a key function
# expect:
# a
# bbb
words = ["bbb", "a", "cc"]
print(min(words, key=len))
print(max(words, key=len))
