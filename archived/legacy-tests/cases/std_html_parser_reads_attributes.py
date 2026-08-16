# probes: HTMLParser reports tag attributes
# expect:
# [('a', [('href', 'x'), ('id', 'link')])]
from html.parser import HTMLParser


class Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found = []

    def handle_starttag(self, tag, attrs):
        self.found.append((tag, sorted(attrs)))


parser = Collector()
parser.feed("<a href='x' id='link'>t</a>")
print(parser.found)
