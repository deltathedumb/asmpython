# expect:
# 2
# 1
# 256
# 6
# 80

import socket

print(socket.AF_INET)
print(socket.SOCK_STREAM)

print(socket.htons(1))
print(socket.IPPROTO_TCP)
print(socket.PORT_HTTP)
