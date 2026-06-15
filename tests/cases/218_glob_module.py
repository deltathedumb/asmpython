# expect:
# hello
# he[[]l[]]lo

import glob

escaped: str = glob.escape("hello")
print(escaped)

escaped2: str = glob.escape("he[l]lo")
print(escaped2)
