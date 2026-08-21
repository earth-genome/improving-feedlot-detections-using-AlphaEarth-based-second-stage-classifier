"""Tiles an AOI and exports data from GEE to GeoTiffs for supported collections.

This is a utility function for uses outside the main ML workflow.

Usage:
`python gee_data_pull.py my_aoi.geojson --start_date 2024-01-01 --end_date 2024-01-31 --collection AlphaEarth --tilesize 224`

"""

import argparse
from dataclasses import fields
from pathlib import Path
import re
import sys

import geopandas as gpd
from tqdm import tqdm

# Make the sibling `gee` package importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gee"))

import gee
from tile_utils import tiles_for_geometry


def valid_date(s: str) -> str:
    """Validate date string in YYYY-MM-DD format and return it unchanged."""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    raise argparse.ArgumentTypeError(f"Not a valid date: '{s}'.")


def main(args):
    """Pull raster data from Earth Engine."""
    tiles_written = []
    tiles_skipped = 0

    # Extractor handles requests to GEE for the configured collection/dates.
    extractor = gee.GEE_Data_Extractor(
        args.start_date,
        args.end_date,
        args.config
    )

    # Read the AOI polygons in WGS84.
    gdf = gpd.read_file(args.geojson_path).to_crs("EPSG:4326")

    # Output directory: <geojson name> + <collection name>.
    outdir = Path(args.geojson_path.split('.geojson')[0] + args.collection)
    outdir.mkdir(parents=True, exist_ok=True)

    # Tile each geometry and export every intersecting tile.
    for idx, row in tqdm(gdf.iterrows(), total=len(gdf), desc="Geometries"):
        geom = row.geometry
        if geom.is_empty or not geom.is_valid:
            continue
        tiles = tiles_for_geometry(
            geom,
            extractor.config.tilesize,
            extractor.config.pad
        )

        for tile in tqdm(tiles):
            # Skip tiles that only touch the bounding box, not the geometry.
            if not row.geometry.intersects(tile.geometry):
                continue

            # Skip tiles already written in a previous run (resumable).
            tif_name = (f"{extractor.config.collection}_{tile.key}_"
                        f"{extractor.start_date}_{extractor.end_date}.tif")
            if (outdir / tif_name).exists():
                tiles_skipped += 1
                continue

            # Request the tile and save it, continuing past per-tile failures.
            try:
                pixels = extractor.get_tile_data(tile)
                extractor.save_tile(pixels, tile, outdir)
                tiles_written.append(tile)
            except Exception as e:
                print(f"Tile {tile.key} failed: {e}")

    print(f"{len(tiles_written)} tiles written from {len(gdf)} geometries "
          f"({tiles_skipped} already present, skipped).")
    return tiles_written


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=("Pull raster data from GEE."))

    # Required args
    parser.add_argument(
        "geojson_path", type=str,
        help="GeoJSON polygons for which to pull raster data.",
    )
    parser.add_argument(
        "--start_date", type=valid_date, required=True,
        help="Start date in YYYY-MM-DD format")
    parser.add_argument(
        "--end_date", type=valid_date, required=True,
        help="End date in YYYY-MM-DD format")

    # DataConfig args (defaults come from gee.DataConfig).
    data_defaults = gee.DataConfig()

    parser.add_argument("--tilesize", type=int,
                        default=data_defaults.tilesize,
                        help="Tile width in pixels for requests to GEE")
    parser.add_argument("--pad", type=int,
                        default=data_defaults.pad,
                        help="Number of pixels to pad each tile")
    parser.add_argument("--collection", type=str,
                        default=data_defaults.collection,
                        choices=gee.DataConfig.available_collections(),
                        help="Satellite image collection")
    parser.add_argument("--clear_threshold", type=float,
                        default=data_defaults.clear_threshold,
                        help="Clear sky (cloud absence) threshold")
    parser.add_argument("--max_workers", type=int,
                        default=data_defaults.max_workers,
                        help="Maximum concurrent GEE requests")

    args = parser.parse_args()

    # Assemble the DataConfig from parsed CLI args.
    config_dict = {
        f.name: getattr(args, f.name, None) for f in fields(gee.DataConfig)
    }

    args.config = gee.DataConfig(**config_dict)
    main(args)
