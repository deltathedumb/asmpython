# probes: a named group is reachable by name
# expect:
# ada
# host
import re

m = re.match(r"(?P<user>\w+)@(?P<host>\w+)", "ada@host")
print(m.group("user"))
print(m.group("host"))
