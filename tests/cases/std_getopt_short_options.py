# probes: getopt parses short options
# expect:
# [('-a', '1')]
# ['extra']
import getopt

opts, rest = getopt.getopt(["-a", "1", "extra"], "a:")
print(opts)
print(rest)
