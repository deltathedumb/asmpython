# probes: textwrap.wrap breaks at the given width
# expect:
# ['aaa bbb', 'ccc ddd']
import textwrap

print(textwrap.wrap("aaa bbb ccc ddd", width=7))
