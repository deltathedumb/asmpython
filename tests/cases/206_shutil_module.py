# expect:
# test.zip
# 80

import shutil

name = shutil.make_archive('test', 'zip')
print(name)
size = shutil.get_terminal_size([80, 24])
print(size[0])
