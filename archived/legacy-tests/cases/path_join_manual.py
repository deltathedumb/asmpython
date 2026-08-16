# expect:
# usr/local/bin
def join_path(*parts):
    return '/'.join(p.strip('/') for p in parts)
print(join_path('usr', 'local', 'bin'))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
