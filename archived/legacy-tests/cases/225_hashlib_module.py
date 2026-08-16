# expect:
# 64
# b94d27
# 900150983cd24fb0d6963f7d28e17f72
# True

import hashlib

h = hashlib.sha256()
h.update_str("hello world")
hx = h.hexdigest()
print(len(hx))
print(hx[:6])

m = hashlib.md5()
m.update_str("abc")
print(m.hexdigest())

h2 = hashlib.sha256()
h2.update_str("")
hx2 = h2.hexdigest()
print(hx2 == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
