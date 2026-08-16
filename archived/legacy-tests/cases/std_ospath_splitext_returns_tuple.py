# probes: os.path.splitext returns a tuple
# expect:
# ('file.tar', '.gz')
# tuple
import os.path

parts = os.path.splitext("file.tar.gz")
print(parts)
print(type(parts).__name__)
