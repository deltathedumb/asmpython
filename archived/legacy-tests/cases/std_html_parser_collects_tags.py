# probes: HTMLParser reports start tags and data
# expect:
# ['start:p', 'data:hello', 'start:b', 'data:world', 'end:b', 'end:p']
from html.parser import HTMLParser


class Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.events = []

    def handle_starttag(self, tag, attrs):
        self.events.append("start:" + tag)

    def handle_endtag(self, tag):
        self.events.append("end:" + tag)

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.events.append("data:" + text)


parser = Collector()
parser.feed("<p>hello <b>world</b></p>")
print(parser.events)
