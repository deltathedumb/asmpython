# expect:
# 192.168.1.1
# 1
# 0
# 1
# 0
# 24
# 256

import ipaddress

addr: ipaddress.IPv4Address = ipaddress.ip_address("192.168.1.1")
print(str(addr))
print(addr.is_private)
print(addr.is_loopback)

loopback: ipaddress.IPv4Address = ipaddress.ip_address("127.0.0.1")
print(loopback.is_loopback)
print(loopback.is_private)

net: ipaddress.IPv4Network = ipaddress.ip_network("192.168.0.0/24", strict=0)
print(net.prefixlen)
print(net.num_addresses)
