"""High-energy physics stack: ROOT files, jagged arrays, Lorentz vectors, PDG tables, fits."""

import tempfile
from pathlib import Path

import awkward as ak
import hepunits
import iminuit
import numpy as np
import particle
import uproot
import vector
from hepunits import units


def root_round_trip(directory: Path) -> None:
    path = directory / "events.root"
    pt = ak.Array([[10.5, 20.0], [], [5.25, 7.5, 30.0]])
    eta = ak.Array([[0.5, -1.0], [], [0.1, 2.0, -0.3]])
    with uproot.recreate(path) as file:
        file["events"] = {"pt": pt, "eta": eta}
    with uproot.open(path) as file:
        tree = file["events"]
        print("tree", tree.num_entries, sorted(tree.keys()))
        events = tree.arrays()
    print("num", ak.num(events.pt).tolist())
    print("flatten", ak.flatten(events.pt).tolist())
    print("central", ak.sum(abs(events.eta) < 1.0, axis=1).tolist())
    print("selected", events.pt[events.pt > 8].tolist())


def lorentz() -> None:
    muon1 = vector.obj(pt=30.0, eta=0.5, phi=0.1, mass=0.10566)
    muon2 = vector.obj(pt=25.0, eta=-0.3, phi=3.0, mass=0.10566)
    total = muon1 + muon2
    print("mass", round(total.mass, 4), "pt", round(total.pt, 4))
    boost = total.to_beta3()
    print("boosted E", round(muon1.boost(-boost).E, 4))
    arrays = vector.zip({"px": ak.Array([[1.0, 2.0], [3.0]]), "py": ak.Array([[0.0, 1.0], [4.0]]),
                         "pz": ak.Array([[0.0, 2.0], [0.0]]),
                         "E": ak.Array([[2.0, 4.0], [6.0]])})  # fmt: skip
    print("jagged mass", ak.round(arrays.mass, 4).tolist())


def pdg_tables() -> None:
    pion = particle.Particle.from_pdgid(211)
    print("pion", pion.name, round(pion.mass, 3), pion.charge)
    muons = particle.Particle.findall(
        lambda p: p.pdgid.is_lepton and abs(p.charge) == 1 and p.name.startswith("mu")
    )
    print("muons", sorted(p.name for p in muons))


def units_and_fit() -> None:
    print("units", round(1.5 * units.GeV / units.MeV, 3),
          round(hepunits.c_light / (units.mm / units.ns), 3))
    x = np.linspace(0.0, 9.0, 10)
    y = 2.0 * x + 1.0 + np.array([0.1, -0.1, 0.05, -0.05, 0.1, -0.1, 0.05, -0.05, 0.1, -0.1])

    def chi2(slope, offset):
        return float(np.sum(((y - (slope * x + offset)) / 0.1) ** 2))

    fit = iminuit.Minuit(chi2, slope=1.0, offset=0.0)
    fit.errordef = iminuit.Minuit.LEAST_SQUARES
    fit.migrad()
    fit.hesse()
    print("fit valid", fit.valid, [round(v, 3) for v in fit.values],
          [round(e, 3) for e in fit.errors])


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root_round_trip(Path(directory))
    lorentz()
    pdg_tables()
    units_and_fit()
