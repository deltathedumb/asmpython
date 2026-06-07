# expect:
# nonempty
import os

# PATH is virtually always set; check it's nonempty by getting len.
v = os.getenv("PATH")
if len(v) > 0:
    print("nonempty")
else:
    print("empty")
