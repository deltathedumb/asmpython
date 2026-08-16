# expect:
# ('file.tar', '.gz')
import os.path
print(os.path.splitext('file.tar.gz'))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
