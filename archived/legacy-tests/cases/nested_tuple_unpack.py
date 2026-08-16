# expect:
# 1 2 3
(a, (b, c)) = (1, (2, 3))
print(a, b, c)
# nested tuple-unpack target (a,(b,c)) is rejected ([E115]).
