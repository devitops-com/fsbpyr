"""matplotlib and seaborn on the Agg backend: PNG and SVG files, hashed."""

import hashlib
import os
import tempfile
from pathlib import Path


def render(directory: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import seaborn as sns

    directory = directory / "out"
    directory.mkdir()
    matplotlib.rcParams["svg.hashsalt"] = "corpus"
    rng = np.random.default_rng(0)
    print("backend", matplotlib.get_backend())

    figure, (line, hist, image) = plt.subplots(1, 3, figsize=(9, 3), dpi=80)
    x = np.linspace(0, 2 * np.pi, 50)
    line.plot(x, np.sin(x), label="sin")
    line.legend()
    line.set_title("line")
    hist.hist(rng.normal(size=500), bins=20)
    hist.set_title("histogram")
    shown = image.imshow(rng.random((8, 8)), cmap="viridis")
    image.text(3, 3, "text", color="white")
    figure.colorbar(shown, ax=image)
    figure.savefig(directory / "plots.png", metadata={"Software": None})
    figure.savefig(directory / "plots.svg", metadata={"Date": None})
    plt.close(figure)

    frame = pd.DataFrame({"group": list("abcd") * 25, "value": rng.normal(size=100)})
    grid = sns.catplot(data=frame, x="group", y="value", kind="box")
    grid.savefig(directory / "seaborn.png", metadata={"Software": None})
    sns.histplot(data=frame, x="value")
    plt.gcf().savefig(directory / "hist.svg", metadata={"Date": None})
    plt.close("all")

    for path in sorted(directory.iterdir()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        print(path.name, path.stat().st_size, digest)


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        os.environ["MPLCONFIGDIR"] = directory  # before the import: the font cache goes there
        render(Path(directory))
