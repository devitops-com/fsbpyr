"""hydra's compose API over a config package, instantiate, and plugin discovery."""

from hydra import compose, initialize_config_module
from hydra.core.plugins import Plugins
from hydra.plugins.launcher import Launcher
from hydra.plugins.sweeper import Sweeper
from hydra.utils import instantiate
from omegaconf import OmegaConf


class Pool:
    def __init__(self, size: int, label: str) -> None:
        self.size = size
        self.label = label

    def describe(self) -> str:
        return f"{self.label}:{self.size}"


def main() -> None:
    with initialize_config_module(config_module="corpus_hydra.conf", version_base=None):
        default = compose("config")
        print("default", OmegaConf.to_yaml(default).strip().replace("\n", " | "))
        changed = compose("config", overrides=["db=postgres", "db.port=6543", "+extra=1"])
        print("override", OmegaConf.to_yaml(changed).strip().replace("\n", " | "))

    pool = instantiate({"_target_": "corpus_hydra.cli.Pool", "size": 3, "label": "main"})
    print("instantiate", pool.describe())

    plugins = Plugins.instance()
    for kind in (Sweeper, Launcher):
        print("plugins", kind.__name__,
              sorted(plugin.__name__ for plugin in plugins.discover(kind)))
