"""shapely, pyproj, geopandas, pyogrio and rtree: GEOS, PROJ and GDAL wheels with their data
directories (proj.db), and libspatialindex loaded with ctypes."""

import tempfile
from pathlib import Path

import geopandas as gpd
import pyproj
import rtree.index
import shapely
from shapely.geometry import Point, box


def shapes() -> None:
    square = box(0, 0, 2, 2)
    union = square.union(box(1, 1, 3, 3))
    print("area", square.area, union.area, round(Point(0, 0).buffer(1).area, 3))
    print("shapely", union.bounds, shapely.intersects(square, Point(1, 1)))


def projections() -> None:
    crs = pyproj.CRS.from_epsg(3857)
    print("crs", crs.name, crs.is_projected)
    transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x, y = transformer.transform(10.0, 50.0)
    print("transform", round(x, 2), round(y, 2))


def frames(root: Path) -> None:
    frame = gpd.GeoDataFrame(
        {"name": ["a", "b"]}, geometry=[Point(10.0, 50.0), Point(11.0, 51.0)], crs="EPSG:4326"
    )
    projected = frame.to_crs(3857)
    print("to_crs", projected.crs.to_epsg(), [round(v, 1) for v in projected.geometry.x])
    for name, driver in (("a.gpkg", "GPKG"), ("a.geojson", "GeoJSON")):
        frame.to_file(root / name, driver=driver, engine="pyogrio")
        back = gpd.read_file(root / name, engine="pyogrio")
        print("file", driver, back.name.tolist(), back.crs.to_epsg(), back.geometry.iloc[1].wkt)


def spatial_index() -> None:
    index = rtree.index.Index()
    for number, (x, y) in enumerate([(0, 0), (1, 1), (5, 5), (9, 9)]):
        index.insert(number, (x, y, x, y))
    print("intersection", sorted(index.intersection((0, 0, 2, 2))))
    print("nearest", list(index.nearest((6, 6), 2)))


def main() -> None:
    shapes()
    projections()
    with tempfile.TemporaryDirectory() as directory:
        frames(Path(directory))
    spatial_index()
