# probes: ElementTree parses a document
# expect:
# root
# ['a', 'b']
# ['1', '2']
import xml.etree.ElementTree as ET

root = ET.fromstring("<root><item name='a'>1</item><item name='b'>2</item></root>")
print(root.tag)
print([item.get("name") for item in root])
print([item.text for item in root])
