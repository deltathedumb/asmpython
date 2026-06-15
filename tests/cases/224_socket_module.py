# expect:
# 2
# 1
# 256
# 127.0.0.1

import socket

print(socket.AF_INET)
print(socket.SOCK_STREAM)

print(socket.htons(1))
print(socket.gethostbyname("localhost"))
