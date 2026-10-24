"""pyomo: concrete models solved through the HiGHS interface;
pyomo.environ loads plugins by name."""

import pyomo.environ as pyo


def solver() -> pyo.SolverFactory:
    return pyo.SolverFactory("appsi_highs")


def lp() -> None:
    model = pyo.ConcreteModel()
    model.x = pyo.Var(domain=pyo.NonNegativeReals)
    model.y = pyo.Var(domain=pyo.NonNegativeReals)
    model.limit = pyo.Constraint(expr=model.x + 2 * model.y <= 14)
    model.ratio = pyo.Constraint(expr=3 * model.x - model.y >= 0)
    model.spread = pyo.Constraint(expr=model.x - model.y <= 2)
    model.cost = pyo.Objective(expr=3 * model.x + 4 * model.y, sense=pyo.maximize)
    result = solver().solve(model)
    print("lp", result.solver.termination_condition, round(pyo.value(model.cost), 4),
          round(pyo.value(model.x), 4), round(pyo.value(model.y), 4))


def mip() -> None:
    items = {"a": (10, 5), "b": (13, 7), "c": (7, 3), "d": (8, 4)}
    model = pyo.ConcreteModel()
    model.names = pyo.Set(initialize=sorted(items))
    model.take = pyo.Var(model.names, domain=pyo.Binary)
    model.weight = pyo.Constraint(expr=sum(items[i][1] * model.take[i] for i in model.names) <= 12)
    model.value = pyo.Objective(
        expr=sum(items[i][0] * model.take[i] for i in model.names), sense=pyo.maximize
    )
    result = solver().solve(model)
    print("mip", result.solver.termination_condition, round(pyo.value(model.value), 4),
          {i: round(pyo.value(model.take[i])) for i in model.names})


def main() -> None:
    print("pyomo", pyo.__name__, "solver available", solver().available())
    print("registered", "appsi_highs" in pyo.SolverFactory)
    lp()
    mip()
