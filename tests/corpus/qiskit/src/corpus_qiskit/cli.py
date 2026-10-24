"""qiskit: circuits, transpiler stage plugins from entry points, the Aer simulator."""

from qiskit import QuantumCircuit, transpile
from qiskit.providers.fake_provider import GenericBackendV2
from qiskit.quantum_info import Statevector
from qiskit.transpiler.preset_passmanagers.plugin import list_stage_plugins
from qiskit_aer import AerSimulator


def bell() -> QuantumCircuit:
    circuit = QuantumCircuit(2, 2)
    circuit.h(0)
    circuit.cx(0, 1)
    circuit.measure([0, 1], [0, 1])
    return circuit


def ghz(qubits: int) -> QuantumCircuit:
    circuit = QuantumCircuit(qubits, qubits)
    circuit.h(0)
    for qubit in range(qubits - 1):
        circuit.cx(qubit, qubit + 1)
    circuit.measure(range(qubits), range(qubits))
    return circuit


def main() -> None:
    backend = GenericBackendV2(num_qubits=5, coupling_map=[[0, 1], [1, 2], [2, 3], [3, 4]], seed=7)
    simulator = AerSimulator()
    for name, circuit in (("bell", bell()), ("ghz4", ghz(4))):
        compiled = transpile(circuit, backend, optimization_level=2, seed_transpiler=11)
        print(name, "depth", circuit.depth(), "transpiled ops",
              sorted(compiled.count_ops().items()))
        result = simulator.run(transpile(circuit, simulator), shots=1024, seed_simulator=5).result()
        print(name, "counts", sorted(result.get_counts().items()))
    state = Statevector.from_label("00").evolve(bell().remove_final_measurements(inplace=False))
    print("statevector", [complex(round(a.real, 6), round(a.imag, 6)) for a in state.data])
    for stage in ("init", "layout", "routing", "translation", "optimization"):
        print("plugins", stage, sorted(list_stage_plugins(stage)))
