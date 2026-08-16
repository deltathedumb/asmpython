# probes: methodcaller forwards its extra arguments
# expect:
# bbnbnb
import operator

print(operator.methodcaller("replace", "a", "b")("banana"))
