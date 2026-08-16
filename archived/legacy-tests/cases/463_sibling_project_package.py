# expect:
# 42

from tests.project_import_fixture import ProjectThing


thing = ProjectThing(42)
print(thing.value)
