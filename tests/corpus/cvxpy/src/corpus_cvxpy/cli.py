"""cvxpy: an LP, a QP, an SOCP and an SDP, each through another solver's compiled extension."""

import cvxpy as cp
import numpy as np


def lp() -> float:
    x = cp.Variable(2)
    problem = cp.Problem(cp.Minimize(-x[0] - 2 * x[1]),
                         [x[0] + x[1] <= 4, x[0] <= 3, x[1] <= 3, x >= 0])
    return problem.solve(solver=cp.CLARABEL)


def qp() -> float:
    q = np.array([[2.0, 0.5], [0.5, 1.0]])
    x = cp.Variable(2)
    problem = cp.Problem(cp.Minimize(cp.quad_form(x, q) - x[0] - x[1]), [x >= 0, cp.sum(x) <= 2])
    return problem.solve(solver=cp.OSQP, eps_abs=1e-8, eps_rel=1e-8)


def socp() -> float:
    x = cp.Variable(2)
    problem = cp.Problem(cp.Minimize(x[0] + x[1]), [cp.norm(x - np.array([3.0, 4.0])) <= 1])
    return problem.solve(solver=cp.CLARABEL)


def sdp() -> float:
    x = cp.Variable((2, 2), PSD=True)
    problem = cp.Problem(cp.Minimize(cp.trace(np.array([[2.0, 1.0], [1.0, 3.0]]) @ x)),
                         [x[0, 1] == 0.5, cp.trace(x) == 2])
    return problem.solve(solver=cp.SCS, eps=1e-8)


def main() -> None:
    print("installed solvers", sorted(cp.installed_solvers()))
    for name, solve in [("lp", lp), ("qp", qp), ("socp", socp), ("sdp", sdp)]:
        print(name, round(float(solve()), 4))
