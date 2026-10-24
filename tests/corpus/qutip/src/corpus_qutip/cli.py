"""qutip: a driven two-level system, the steady state of a damped cavity, a fidelity."""

import numpy as np
import qutip


def driven_qubit() -> None:
    hamiltonian = 0.5 * 2 * np.pi * qutip.sigmax()
    times = np.linspace(0.0, 1.0, 5)
    result = qutip.mesolve(
        hamiltonian, qutip.basis(2, 0), times, [0.1 * qutip.sigmam()],
        e_ops=[qutip.sigmaz()], options={"progress_bar": False},
    )  # fmt: skip
    print("sigmaz", np.round(result.expect[0], 4).tolist())


def damped_cavity() -> None:
    n = 8
    a = qutip.destroy(n)
    hamiltonian = a.dag() * a + 0.2 * (a + a.dag())
    rho = qutip.steadystate(hamiltonian, [np.sqrt(0.5) * a])
    print("cavity photons", round(float(qutip.expect(a.dag() * a, rho)), 4),
          "trace", round(rho.tr().real, 6))


def fidelity() -> None:
    plus = (qutip.basis(2, 0) + qutip.basis(2, 1)).unit()
    print("fidelity", round(qutip.fidelity(plus, qutip.basis(2, 0)), 4))
    print("coherent", round(qutip.fidelity(qutip.coherent(10, 1.0), qutip.coherent(10, 1.2)), 4))


def main() -> None:
    driven_qubit()
    damped_cavity()
    fidelity()
