"""openmm: a small molecular dynamics system; openmm loads its plugins as shared libraries."""

import openmm
from openmm import unit


def build_system() -> tuple[openmm.System, list[openmm.Vec3]]:
    system = openmm.System()
    bonds = openmm.HarmonicBondForce()
    nonbonded = openmm.NonbondedForce()
    nonbonded.setNonbondedMethod(openmm.NonbondedForce.NoCutoff)
    for _ in range(4):
        system.addParticle(12.0)
        nonbonded.addParticle(0.1, 0.3, 0.5)
    for first in range(3):
        bonds.addBond(first, first + 1, 0.15, 1000.0)
    system.addForce(bonds)
    system.addForce(nonbonded)
    positions = [openmm.Vec3(0.0, 0.0, 0.0), openmm.Vec3(0.17, 0.0, 0.0),
                 openmm.Vec3(0.17, 0.16, 0.0), openmm.Vec3(0.0, 0.16, 0.05)]  # fmt: skip
    return system, positions


def main() -> None:
    names = sorted(
        openmm.Platform.getPlatform(i).getName() for i in range(openmm.Platform.getNumPlatforms())
    )
    print("platforms", names)
    system, positions = build_system()
    integrator = openmm.LangevinMiddleIntegrator(
        300 * unit.kelvin, 1 / unit.picosecond, 0.002 * unit.picoseconds
    )
    integrator.setRandomNumberSeed(42)
    context = openmm.Context(system, integrator, openmm.Platform.getPlatformByName("Reference"))
    context.setPositions(positions)
    context.setVelocitiesToTemperature(300 * unit.kelvin, 7)
    for label, steps in (("start", 0), ("after 100 steps", 100)):
        integrator.step(steps)
        state = context.getState(getEnergy=True)
        potential = state.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)
        kinetic = state.getKineticEnergy().value_in_unit(unit.kilojoule_per_mole)
        print(label, "potential", round(potential, 3), "kinetic", round(kinetic, 3))
