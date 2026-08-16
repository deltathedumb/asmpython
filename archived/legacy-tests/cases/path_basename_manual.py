# expect:
# file.txt
def basename(path):
    return path.split('/')[-1]
print(basename('/a/b/file.txt'))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
