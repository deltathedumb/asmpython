# probes: shlex.split respects quoting
# expect:
# ['a', 'b c', 'd']
import shlex

print(shlex.split('a "b c" d'))
