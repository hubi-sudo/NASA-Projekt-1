from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path

import rasterio
from rasterio.warp import transform_geom
from rasterio.windows import Window

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
LAYERS = [*(f'sr_band{i}' for i in range(2, 8)), 'pixel_qa', 'radsat_qa']
NOMINATIM = 'https://nominatim.openstreetmap.org/search?'
USER_AGENT = {'User-Agent': 'landsat-miasta (projekt studencki AGH)'}


def fetch_boundary(city: str) -> dict:
    query = urllib.parse.urlencode({'city': city, 'country': 'Polska', 'format': 'geojson',
                                    'polygon_geojson': 1, 'polygon_threshold': 0.0003, 'limit': 5})
    with urllib.request.urlopen(urllib.request.Request(NOMINATIM + query, headers=USER_AGENT)) as r:
        found = json.load(r)['features']
    for f in found:
        p = f['properties']
        if p['osm_type'] == 'relation' and p['category'] == 'boundary' and f['geometry']['type'].endswith('Polygon'):
            return {'type': 'Feature', 'geometry': f['geometry'],
                    'properties': {'name': city, 'osm_id': p['osm_id'], 'source': '© OpenStreetMap contributors, ODbL'}}
    raise ValueError(f'Nie znaleziono granicy administracyjnej: {city}')


def boundaries(region: dict, refresh=False) -> dict:
    path = DATA / 'boundaries' / f"{region['id']}.geojson"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding='utf-8'))
    features = []
    for city in region['cities']:
        features.append(fetch_boundary(city))
        time.sleep(1.1)
    fc = {'type': 'FeatureCollection', 'features': features}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fc, ensure_ascii=False), encoding='utf-8')
    return fc


def region_window(src, fc: dict, buffer_km: float) -> Window:
    xs, ys = [], []
    for f in fc['features']:
        g = transform_geom('EPSG:4326', src.crs, f['geometry'])
        rings = g['coordinates'] if g['type'] == 'Polygon' else [r for p in g['coordinates'] for r in p]
        for ring in rings:
            xs += [c[0] for c in ring]
            ys += [c[1] for c in ring]
    b = buffer_km * 1000
    inv = ~src.transform
    c0, r0 = inv * (min(xs) - b, max(ys) + b)
    c1, r1 = inv * (max(xs) + b, min(ys) - b)
    c0, r0 = max(0, math.floor(c0)), max(0, math.floor(r0))
    c1, r1 = min(src.width, math.ceil(c1)), min(src.height, math.ceil(r1))
    return Window(c0, r0, c1 - c0, r1 - r0)


def crop_scene(scene: Path, region: dict, fc: dict) -> list[dict]:
    target = DATA / 'scene' / region['id']
    target.mkdir(parents=True, exist_ok=True)
    files = []
    with rasterio.open(next(scene.glob('LC08*_pixel_qa.tif'))) as ref:
        window = region_window(ref, fc, region['buffer_km'])
    for layer in LAYERS:
        src_path = next(scene.glob(f'LC08*_{layer}.tif'))
        with rasterio.open(src_path) as src:
            profile = src.profile.copy()
            profile.update(width=int(window.width), height=int(window.height),
                           transform=src.window_transform(window),
                           compress='deflate', predictor=2, tiled=True, blockxsize=256, blockysize=256)
            out = target / src_path.name
            with rasterio.open(out, 'w', **profile) as dst:
                dst.write(src.read(1, window=window), 1)
        files.append({'path': out.relative_to(DATA).as_posix(),
                      'sha256': hashlib.sha256(out.read_bytes()).hexdigest()})
    print(f"{region['name']}: {int(window.width)} x {int(window.height)} px")
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scene', type=Path, default=ROOT.parent / 'NASA' / 'NASA' / 'Landsat8' / 'LC08',
                        help='katalog pełnej sceny LC08 (Collection 1, Surface Reflectance)')
    parser.add_argument('--refresh-boundaries', action='store_true', help='pobierz granice z OSM ponownie')
    args = parser.parse_args()
    config = json.loads((DATA / 'regions.json').read_text(encoding='utf-8'))
    xml = next(args.scene.glob('LC08*.xml'))
    (DATA / 'scene').mkdir(parents=True, exist_ok=True)
    shutil.copy(xml, DATA / 'scene' / xml.name)
    manifest = {'source_scene': config['scene'], 'acquisition_date': '2013-08-07',
                'source': 'USGS/EROS, Landsat 8 OLI Collection 1 Level-2 (ESPA Surface Reflectance)',
                'boundaries': 'OpenStreetMap (Nominatim), © OpenStreetMap contributors, ODbL',
                'note': 'Wycinki prostokątne: granice miast + bufor, rozdzielczość 30 m, oryginalne DN.',
                'regions': {}}
    for region in config['regions']:
        fc = boundaries(region, args.refresh_boundaries)
        manifest['regions'][region['id']] = crop_scene(args.scene, region, fc)
    (DATA / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
