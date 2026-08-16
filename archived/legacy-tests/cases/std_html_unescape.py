# probes: html.unescape resolves entities
# expect:
# <tag> & A
import html

print(html.unescape("&lt;tag&gt; &amp; &#65;"))
