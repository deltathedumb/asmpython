# probes: itemgetter with several indices returns a tuple
# expect:
# ('c', 'a')
import operator

print(operator.itemgetter(2, 0)("abc"))
