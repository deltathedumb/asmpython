# probe131: complex class interaction with list of instances
class Employee:
    def __init__(self, name: str, dept: str, salary: int) -> None:
        self.name = name
        self.dept = dept
        self.salary = salary

    def __str__(self) -> str:
        return f"{self.name}({self.dept}, {self.salary})"

    def __lt__(self, other: "Employee") -> bool:
        return self.salary < other.salary

employees = [
    Employee("Alice", "Eng", 90000),
    Employee("Bob", "HR", 70000),
    Employee("Charlie", "Eng", 85000),
    Employee("Diana", "HR", 75000),
    Employee("Eve", "Eng", 95000),
]

# Filter by dept
eng = [e for e in employees if e.dept == "Eng"]
print(len(eng))   # 3

# Sort by salary
eng.sort()
print(str(eng[0]))   # Charlie(Eng, 85000)
print(str(eng[2]))   # Eve(Eng, 95000)

# Group by dept
by_dept: dict = {}
for e in employees:
    if e.dept not in by_dept:
        by_dept[e.dept] = []
    by_dept[e.dept].append(e)

print(len(by_dept["Eng"]))   # 3
print(len(by_dept["HR"]))    # 2

# Aggregate salary by dept
dept_totals: dict = {}
for dept in by_dept:
    total: int = 0
    for e in by_dept[dept]:
        total += e.salary
    dept_totals[dept] = total

print(dept_totals["Eng"])   # 270000
print(dept_totals["HR"])    # 145000

# Best-paid per dept
for dept in by_dept:
    best = max(by_dept[dept], key=lambda e: e.salary)
    print(f"{dept}: {best.name}")
# Eng: Eve, HR: Diana
