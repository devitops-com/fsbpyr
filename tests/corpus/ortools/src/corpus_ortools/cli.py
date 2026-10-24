"""ortools: CP-SAT, the routing library and GLOP through pywraplp (native libraries)."""

from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from ortools.linear_solver import pywraplp
from ortools.sat.python import cp_model

DISTANCES = [
    [0, 9, 14, 7, 12],
    [9, 0, 6, 11, 10],
    [14, 6, 0, 8, 13],
    [7, 11, 8, 0, 5],
    [12, 10, 13, 5, 0],
]


def cp_sat() -> None:
    model = cp_model.CpModel()
    x, y, z = (model.new_int_var(0, 10, name) for name in "xyz")
    model.add(x + 2 * y + 3 * z <= 20)
    model.add(x != y)
    model.add_all_different([x, y, z])
    model.maximize(3 * x + 2 * y + z)
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = 7
    status = solver.solve(model)
    print("cp-sat", solver.status_name(status), solver.value(x), solver.value(y), solver.value(z),
          solver.objective_value)


def tsp() -> None:
    manager = pywrapcp.RoutingIndexManager(len(DISTANCES), 1, 0)
    routing = pywrapcp.RoutingModel(manager)
    callback = routing.RegisterTransitCallback(
        lambda i, j: DISTANCES[manager.IndexToNode(i)][manager.IndexToNode(j)]
    )
    routing.SetArcCostEvaluatorOfAllVehicles(callback)
    parameters = pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    solution = routing.SolveWithParameters(parameters)
    index, route = routing.Start(0), []
    while not routing.IsEnd(index):
        route.append(manager.IndexToNode(index))
        index = solution.Value(routing.NextVar(index))
    print("tsp route", [*route, 0], "cost", solution.ObjectiveValue())


def glop() -> None:
    solver = pywraplp.Solver.CreateSolver("GLOP")
    x = solver.NumVar(0, solver.infinity(), "x")
    y = solver.NumVar(0, solver.infinity(), "y")
    solver.Add(x + 2 * y <= 14)
    solver.Add(3 * x - y >= 0)
    solver.Add(x - y <= 2)
    solver.Maximize(3 * x + 4 * y)
    status = solver.Solve()
    print("glop", status == pywraplp.Solver.OPTIMAL, round(solver.Objective().Value(), 4),
          round(x.solution_value(), 4), round(y.solution_value(), 4))


def main() -> None:
    cp_sat()
    tsp()
    glop()
