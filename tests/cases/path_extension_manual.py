# expect:
# gz
def get_ext(fname):
    if '.' in fname:
        return fname.rsplit('.', 1)[1]
    return ''
print(get_ext('archive.tar.gz'))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
