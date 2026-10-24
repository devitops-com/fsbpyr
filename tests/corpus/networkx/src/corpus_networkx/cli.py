"""networkx: seeded generators, algorithms with scipy, GraphML files, and backend dispatch."""

import tempfile
from pathlib import Path

import networkx as nx
from networkx.utils import backends


def flow_network() -> nx.DiGraph:
    graph = nx.DiGraph()
    edges = [("s", "a", 10), ("s", "b", 5), ("a", "b", 15), ("a", "t", 10), ("b", "t", 10)]
    for tail, head, capacity in edges:
        graph.add_edge(tail, head, capacity=capacity)
    return graph


def main() -> None:
    graph = nx.barabasi_albert_graph(30, 2, seed=7)
    print("generated", graph.number_of_nodes(), graph.number_of_edges(), nx.is_connected(graph))
    print("shortest path", nx.shortest_path(graph, 0, 25), nx.shortest_path_length(graph, 0, 25))
    ranks = nx.pagerank(graph, alpha=0.85)  # uses scipy's sparse eigen solver
    print("pagerank top",
          [(node, round(ranks[node], 4))
           for node in sorted(ranks, key=lambda n: (-ranks[n], n))[:3]])
    value, _ = nx.maximum_flow(flow_network(), "s", "t")
    print("max flow", value)
    communities = nx.community.louvain_communities(nx.karate_club_graph(), seed=0)
    print("louvain", sorted(sorted(community) for community in communities))
    dag = nx.DiGraph([("shirt", "tie"), ("tie", "jacket"), ("trousers", "belt"),
                      ("belt", "jacket")])
    print("topological", list(nx.lexicographical_topological_sort(dag)))
    print("clustering",
          round(nx.average_clustering(nx.watts_strogatz_graph(20, 4, 0.2, seed=3)), 4))

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "graph.graphml"
        nx.write_graphml(flow_network(), path)
        back = nx.read_graphml(path)
        print("graphml", sorted(back.edges(data="capacity")))

    print("backends", sorted(backends.backends))
    print("dispatch", round(nx.pagerank(nx.path_graph(4), backend="networkx")[0], 4))
