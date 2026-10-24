"""casadi: NLP (ipopt), integrator (cvodes) and QP (qpoases), plugins dlopened by name."""

import casadi as ca


def rosenbrock() -> None:
    x = ca.SX.sym("x", 2)
    nlp = {"x": x, "f": (1 - x[0]) ** 2 + 100 * (x[1] - x[0] ** 2) ** 2}
    solver = ca.nlpsol(
        "s", "ipopt", nlp, {"ipopt.print_level": 0, "print_time": 0, "ipopt.sb": "yes"}
    )
    solution = solver(x0=[-1.0, 1.0])
    print("ipopt", [round(float(v), 4) for v in solution["x"].elements()],
          round(float(solution["f"]), 6))


def integrate() -> None:
    x = ca.SX.sym("x")
    integrator = ca.integrator("i", "cvodes", {"x": x, "ode": -2 * x}, 0.0, 1.0)
    print("cvodes", round(float(integrator(x0=1.0)["xf"]), 6))


def quadratic_program() -> None:
    x = ca.SX.sym("x", 2)
    qp = {"x": x, "f": x[0] ** 2 + x[1] ** 2 - x[0] - 2 * x[1], "g": x[0] + x[1]}
    solver = ca.qpsol("q", "qpoases", qp, {"printLevel": "none"})
    solution = solver(lbg=-ca.inf, ubg=1.0)
    print("qpoases", [round(float(v), 4) for v in solution["x"].elements()],
          round(float(solution["f"]), 6))


def main() -> None:
    print("casadi", ca.has_nlpsol("ipopt"), ca.has_integrator("cvodes"), ca.has_conic("qpoases"))
    rosenbrock()
    integrate()
    quadratic_program()
