# expect:
# 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
# 5d41402abc4b2a76b9719d911017c592

import hashlib

h = hashlib.sha256()
h.update_str("hello")
print(h.hexdigest())

h2 = hashlib.md5()
h2.update_str("hello")
print(h2.hexdigest())
