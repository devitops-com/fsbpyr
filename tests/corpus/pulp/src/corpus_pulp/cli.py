"""pulp: a MIP solved by the CBC executable pulp ships (a subprocess) and by HiGHS (in process)."""

import pulp


def knapsack() -> pulp.LpProblem:
    values = {"a": 10, "b": 13, "c": 7, "d": 8}
    weights = {"a": 5, "b": 7, "c": 3, "d": 4}
    problem = pulp.LpProblem("knapsack", pulp.LpMaximize)
    chosen = {item: pulp.LpVariable(f"take_{item}", cat="Binary") for item in values}
    problem += pulp.lpSum(values[item] * chosen[item] for item in values)
    problem += pulp.lpSum(weights[item] * chosen[item] for item in values) <= 12
    return problem


def report(label: str, solver: pulp.LpSolver) -> None:
    problem = knapsack()
    status = problem.solve(solver)
    print(label, "status", pulp.LpStatus[status], "objective",
          round(pulp.value(problem.objective), 4))
    print(label, "values",
          {variable.name: round(variable.value()) for variable in problem.variables()})


def main() -> None:
    print("solvers", sorted(pulp.listSolvers(onlyAvailable=True)))
    report("cbc", pulp.PULP_CBC_CMD(msg=False))
    report("highs", pulp.HiGHS(msg=False))
