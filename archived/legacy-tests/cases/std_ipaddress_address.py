# probes: IPv4Address parses and compares
# expect:
# 10.0.0.1
# 167772161
# True
import ipaddress

a = ipaddress.IPv4Address("10.0.0.1")
print(str(a))
print(int(a))
print(a < ipaddress.IPv4Address("10.0.0.2"))
