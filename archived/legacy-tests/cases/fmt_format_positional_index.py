# probes: numbered fields may repeat and reorder
# expect:
# b-a-b
print("{1}-{0}-{1}".format("a", "b"))
