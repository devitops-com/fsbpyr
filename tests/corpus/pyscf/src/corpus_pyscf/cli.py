"""pyscf: quantum chemistry with C libraries loaded from the package and bundled basis sets."""

import os
import tempfile


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        os.environ["PYSCF_TMPDIR"] = directory  # read when pyscf.lib is imported
        from pyscf import dft, gto, lib, scf

        lib.num_threads(1)
        print("tmpdir inside run", directory == lib.param.TMPDIR)
        molecule = gto.M(
            atom="O 0 0 0.1173; H 0 0.7572 -0.4692; H 0 -0.7572 -0.4692",
            basis="sto-3g",
            verbose=0,
        )
        print("basis functions", molecule.nao, "electrons", molecule.nelectron)
        hf = scf.RHF(molecule)
        hf.chkfile = os.path.join(directory, "h2o.chk")
        print("RHF energy", round(hf.kernel(), 6), "converged", hf.converged)
        print("chkfile written", os.path.exists(hf.chkfile))
        ks = dft.RKS(molecule)
        ks.xc = "b3lyp"
        ks.grids.level = 1
        ks.chkfile = os.path.join(directory, "h2o-dft.chk")
        print("B3LYP energy", round(ks.kernel(), 6), "converged", ks.converged)
