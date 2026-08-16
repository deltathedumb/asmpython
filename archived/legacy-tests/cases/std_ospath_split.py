# probes: os.path.split separates the last component
# expect:
# ('a/b', 'c.txt')
import os.path

print(os.path.split("a/b/c.txt"))
