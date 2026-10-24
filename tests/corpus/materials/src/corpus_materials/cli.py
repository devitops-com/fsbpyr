"""Atomistic simulation: ase structures, EMT calculator, relaxation, file formats, spglib."""

import tempfile
from pathlib import Path

import numpy as np
import spglib
from ase.build import bulk, molecule
from ase.calculators.emt import EMT
from ase.io import read, write
from ase.optimize import BFGS


def rounded(values, digits=5):
    return (np.round(np.asarray(values), digits) + 0.0).tolist()  # + 0.0: no -0.0


def calculators() -> None:
    copper = bulk("Cu", "fcc", a=3.6)
    copper.calc = EMT()
    print("Cu energy", round(copper.get_potential_energy(), 5))
    copper = copper.repeat((2, 1, 1))
    copper.calc = EMT()
    copper.rattle(stdev=0.05, seed=42)
    print("Cu forces", rounded(copper.get_forces()))
    water = molecule("H2O")
    water.calc = EMT()
    start = water.get_potential_energy()
    BFGS(water, logfile=None).run(fmax=0.05)
    print("H2O relaxed", round(start, 5), round(water.get_potential_energy(), 5))


def file_formats(directory: Path) -> None:
    atoms = bulk("Cu", "fcc", a=3.6, cubic=True)
    atoms.calc = EMT()
    for name in ("cu.extxyz", "cu.cif"):
        write(directory / name, atoms)
        back = read(directory / name)
        print(name, len(back), back.get_chemical_formula(), rounded(back.cell.lengths(), 4))


def symmetry() -> None:
    atoms = bulk("Cu", "fcc", a=3.6)
    cell = (atoms.cell[:], atoms.get_scaled_positions(), atoms.numbers)
    print("spglib", spglib.get_spacegroup(cell, symprec=1e-5))
    dataset = spglib.get_symmetry_dataset(cell, symprec=1e-5)
    print("number", dataset.number, "operations", len(dataset.rotations),
          "wyckoffs", dataset.wyckoffs)


def main() -> None:
    calculators()
    with tempfile.TemporaryDirectory() as directory:
        file_formats(Path(directory))
    symmetry()
