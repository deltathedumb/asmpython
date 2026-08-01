# probes: an IPv4Network contains its members
# expect:
# 4
# True
# False
import ipaddress

net = ipaddress.IPv4Network("10.0.0.0/30")
print(net.num_addresses)
print(ipaddress.IPv4Address("10.0.0.1") in net)
print(ipaddress.IPv4Address("10.0.1.1") in net)
