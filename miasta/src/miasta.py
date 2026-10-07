from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import matplotlib
import numpy as np
import rasterio
from matplotlib.colors import TwoSlopeNorm
from PIL import Image
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.transform import array_bounds, from_bounds
from rasterio.warp import reproject, transform_bounds, transform_geom

ROOT = Path(__file__).resolve().parents[1]
NS = {'e': 'http://espa.cr.usgs.gov/v2'}
WEB_RADIUS = 20037508.342789244
PIXEL_KM2 = 0.03 * 0.03
GAMMA = 0.8

INDICES = {
    'NDBI':  {'full': 'Normalized Difference Built-up Index', 'formula': '(B6 − B5) / (B6 + B5)',
              'cmap': 'RdYlBu_r', 'range': (-0.45, 0.15), 'low': 'roślinność', 'high': 'zabudowa / goła gleba',
              'about': 'Zabudowa i suche powierzchnie odbijają więcej w podczerwieni krótkofalowej (SWIR1, B6) niż w bliskiej (NIR, B5), więc NDBI > 0 wskazuje tereny zabudowane.'},
    'IBI':   {'full': 'Index-based Built-up Index (Xu 2008)', 'formula': '[2·B6/(B6+B5) − (B5/(B5+B4) + B3/(B3+B6))] / [2·B6/(B6+B5) + (B5/(B5+B4) + B3/(B3+B6))]',
              'cmap': 'Spectral_r', 'range': (-0.45, 0.15), 'low': 'roślinność / woda', 'high': 'zabudowa',
              'about': 'Ulepszony indeks zabudowy. Łączy NDBI ze wskaźnikami roślinności i wody, żeby lepiej oddzielić zabudowę.'},
    'MNDWI': {'full': 'Modified Normalized Difference Water Index', 'formula': '(B3 − B6) / (B3 + B6)',
              'cmap': 'BrBG', 'range': (-0.6, 0.6), 'low': 'ląd', 'high': 'woda',
              'about': 'Wody powierzchniowe (MNDWI > 0): Wisła, zalewy, stawy. Przydaje się też do odróżniania zabudowy od pól.'},
}
COMPOSITES = {
    'RGB': {'name': 'Kolory naturalne', 'bands': (4, 3, 2), 'formula': 'R=B4, G=B3, B=B2',
            'about': 'Obraz tak, jak widziałoby go oko. Rozciągnięcie 2–98 percentyla (wspólne dla wszystkich miast) i gamma 0,8.'},
    'CIR': {'name': 'Fałszywe barwy (CIR)', 'bands': (5, 4, 3), 'formula': 'R=B5, G=B4, B=B3',
            'about': 'Bliska podczerwień jako czerwień: roślinność jest czerwona, zabudowa szaroniebieska, woda ciemna.'},
    'SWIR': {'name': 'Barwy miejskie (SWIR)', 'bands': (7, 6, 4), 'formula': 'R=B7, G=B6, B=B4',
             'about': 'Kompozycja „urban”: zabudowa fioletowo-różowa, roślinność zielona, gleba brązowa, woda prawie czarna.'},
}
LAYER_ORDER = ['RGB', 'CIR', 'SWIR', 'NDBI', 'IBI', 'MNDWI']


def read_metadata(xml: Path) -> dict:
    root = ET.parse(xml).getroot()
    product = root.findtext('e:global_metadata/e:product_id', namespaces=NS)
    if not product or '_01_' not in product:
        raise ValueError('Obsługiwany jest wyłącznie Landsat Collection 1 z pixel_qa.')
    bands = {}
    for b in root.findall('e:bands/e:band', NS):
        valid = b.find('e:valid_range', NS)
        bands[b.attrib['name']] = {
            'scale': float(b.get('scale_factor', 1)), 'offset': float(b.get('add_offset', 0)),
            'fill': float(b.get('fill_value', -9999)),
            'valid_min': float(valid.get('min')) if valid is not None else None,
            'valid_max': float(valid.get('max')) if valid is not None else None}
    return {'product': product, 'date': root.findtext('e:global_metadata/e:acquisition_date', namespaces=NS),
            'bands': bands}


def quality_mask(qa: np.ndarray, radsat: np.ndarray) -> np.ndarray:
    bad = (qa & ((1 << 0) | (1 << 3) | (1 << 4) | (1 << 5) | (1 << 10))) != 0
    bad |= ((qa >> 6) & 3) == 3
    bad |= ((qa >> 8) & 3) == 3
    bad |= (radsat & (1 | sum(1 << b for b in range(2, 8)))) != 0
    return ~bad


def load_region(region: dict, meta: dict) -> dict:
    folder = ROOT / 'data' / 'scene' / region['id']
    raw, profile = {}, None
    for key in [*(f'sr_band{i}' for i in range(2, 8)), 'pixel_qa', 'radsat_qa']:
        with rasterio.open(next(folder.glob(f'LC08*_{key}.tif'))) as src:
            if profile is None:
                profile = src.profile.copy()
            elif src.transform != profile['transform'] or src.shape != (profile['height'], profile['width']):
                raise ValueError(f'Niezgodna siatka rastra: {region["id"]}/{key}')
            raw[key] = src.read(1)
    valid = quality_mask(raw['pixel_qa'], raw['radsat_qa'])
    for i in range(2, 8):
        b = meta['bands'][f'sr_band{i}']
        a = raw[f'sr_band{i}']
        valid &= (a != b['fill']) & (a >= b['valid_min']) & (a <= b['valid_max'])
    reflectance = {}
    for i in range(2, 8):
        b = meta['bands'][f'sr_band{i}']
        reflectance[i] = np.where(valid, raw[f'sr_band{i}'].astype('float32') * b['scale'] + b['offset'], np.nan)
    fc = json.loads((ROOT / 'data' / 'boundaries' / f"{region['id']}.geojson").read_text(encoding='utf-8'))
    geoms = [transform_geom('EPSG:4326', profile['crs'], f['geometry']) for f in fc['features']]
    inside = geometry_mask(geoms, out_shape=valid.shape, transform=profile['transform'], invert=True)
    return {'region': region, 'profile': profile, 'valid': valid, 'reflectance': reflectance,
            'boundaries': fc, 'inside': inside}


def nd(a, b):
    out = np.full(a.shape, np.nan, dtype='float32')
    den = a + b
    ok = np.isfinite(a) & np.isfinite(b) & (np.abs(den) > 1e-6)
    np.divide(a - b, den, out=out, where=ok)
    return out


def calculate_indices(r: dict) -> dict:
    with np.errstate(invalid='ignore', divide='ignore'):
        built = 2 * r[6] / (r[6] + r[5])
        other = r[5] / (r[5] + r[4]) + r[3] / (r[3] + r[6])
    return {'NDBI': nd(r[6], r[5]), 'IBI': nd(built, other), 'MNDWI': nd(r[3], r[6]), 'NDVI': nd(r[5], r[4])}


def lut(info: dict) -> np.ndarray:
    lo, hi = info['range']
    norm = TwoSlopeNorm(0.0, lo, hi)
    return (matplotlib.colormaps[info['cmap']](norm(np.linspace(lo, hi, 255)))[:, :3] * 255).round().astype('uint8')


def encode(a: np.ndarray, lo: float, hi: float) -> np.ndarray:
    code = np.rint((np.clip(a, lo, hi) - lo) / (hi - lo) * 254) + 1
    return np.where(np.isfinite(a), code, 0).astype('uint8')


def stretch_limits(regions: list[dict]) -> dict:
    limits = {}
    for b in range(2, 8):
        values = np.concatenate([r['reflectance'][b][r['valid']] for r in regions])
        limits[b] = tuple(float(v) for v in np.percentile(values, (2, 98)))
    return limits


def stretch(a: np.ndarray, limits: tuple) -> np.ndarray:
    lo, hi = limits
    return np.clip((a - lo) / (hi - lo), 0, 1) ** GAMMA


def display_stack(reg: dict, ix: dict, limits: dict) -> tuple[np.ndarray, list[str]]:
    bands, names = [], []
    for name, info in INDICES.items():
        bands.append(encode(ix[name], *info['range']))
        names.append(name)
    for name, comp in COMPOSITES.items():
        for i, b in enumerate(comp['bands']):
            v = np.rint(stretch(reg['reflectance'][b], limits[b]) * 254) + 1
            bands.append(np.where(reg['valid'], v, 0).astype('uint8'))
            names.append(f'{name}{i}')
    return np.stack(bands), names


def describe(a: np.ndarray, mask: np.ndarray) -> dict:
    v = a[mask & np.isfinite(a)]
    return {'mean': round(float(v.mean()), 4), 'median': round(float(np.median(v)), 4),
            'positive_pct': round(float((v > 0).mean() * 100), 2)}


def region_stats(reg: dict, ix: dict) -> dict:
    inside, valid = reg['inside'], reg['valid']
    m = inside & np.isfinite(ix['NDVI']) & np.isfinite(ix['NDBI'])
    return {'area_km2': round(float(inside.sum() * PIXEL_KM2), 1),
            'valid_pct': round(float((inside & valid).sum() / inside.sum() * 100), 2),
            'indices': {k: describe(ix[k], inside) for k in INDICES},
            'corr_ndvi_ndbi': round(float(np.corrcoef(ix['NDVI'][m], ix['NDBI'][m])[0, 1]), 3)}


def tile_ranges(bounds3857, z):
    w, s, e, n = bounds3857
    span = 2 * WEB_RADIUS / 2 ** z
    x0, x1 = math.floor((w + WEB_RADIUS) / span), math.floor((e + WEB_RADIUS) / span)
    y0, y1 = math.floor((WEB_RADIUS - n) / span), math.floor((WEB_RADIUS - s) / span)
    return {(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}


def write_tiles(stacks: list[dict], names: list[str], site: Path, minzoom: int, maxzoom: int) -> list[str]:
    written = []
    for z in range(minzoom, maxzoom + 1):
        span = 2 * WEB_RADIUS / 2 ** z
        method = Resampling.nearest if z == maxzoom else Resampling.average
        tiles = {}
        for k, st in enumerate(stacks):
            for t in tile_ranges(st['bounds3857'], z):
                tiles.setdefault(t, []).append(k)
        for (x, y), members in sorted(tiles.items()):
            bounds = (x * span - WEB_RADIUS, WEB_RADIUS - (y + 1) * span, (x + 1) * span - WEB_RADIUS, WEB_RADIUS - y * span)
            mosaic = np.zeros((len(names), 256, 256), dtype='uint8')
            for k in members:
                part = np.zeros_like(mosaic)
                reproject(stacks[k]['data'], part, src_transform=stacks[k]['transform'], src_crs=stacks[k]['crs'],
                          dst_transform=from_bounds(*bounds, 256, 256), dst_crs='EPSG:3857',
                          resampling=method, src_nodata=0, dst_nodata=0)
                mosaic = np.where(mosaic == 0, part, mosaic)
            if not mosaic.any():
                continue
            written.append(f'{z}/{x}/{y}')
            for layer in LAYER_ORDER:
                folder = site / 'tiles' / layer / str(z) / str(x)
                folder.mkdir(parents=True, exist_ok=True)
                if layer in COMPOSITES:
                    rgb = np.stack([mosaic[names.index(f'{layer}{i}')] for i in range(3)], -1)
                    alpha = (rgb > 0).all(-1)
                    rgb = ((np.clip(rgb.astype(int) - 1, 0, 254)) * 255 // 254).astype('uint8')
                    q = Image.fromarray(rgb, 'RGB').quantize(255, method=Image.Quantize.FASTOCTREE,
                                                             dither=Image.Dither.NONE)
                    codes = np.where(alpha, np.asarray(q).astype('uint16') + 1, 0).astype('uint8')
                    pal = (q.getpalette() or [])[:255 * 3]
                    pal = [0, 0, 0] + pal + [0] * (255 * 3 - len(pal))
                else:
                    codes = mosaic[names.index(layer)]
                    pal = [0, 0, 0] + lut(INDICES[layer]).flatten().tolist()
                img = Image.fromarray(codes, 'P')
                img.putpalette(pal)
                img.save(folder / f'{y}.png', optimize=True, transparency=0)
    return written


def export(output: Path, site: Path, minzoom: int, maxzoom: int) -> dict:
    t0 = time.perf_counter()
    config = json.loads((ROOT / 'data' / 'regions.json').read_text(encoding='utf-8'))
    meta = read_metadata(next((ROOT / 'data' / 'scene').glob('LC08*.xml')))
    regions = [load_region(r, meta) for r in config['regions']]
    limits = stretch_limits(regions)
    results, stacks, names = [], [], None
    for reg in regions:
        ix = calculate_indices(reg['reflectance'])
        results.append({'ix': ix, 'stats': region_stats(reg, ix)})
        data, names = display_stack(reg, ix, limits)
        p = reg['profile']
        stacks.append({'data': data, 'transform': p['transform'], 'crs': p['crs'],
                       'bounds3857': transform_bounds(p['crs'], 'EPSG:3857', *array_bounds(
                           p['height'], p['width'], p['transform']), densify_pts=21)})
        raster_dir = output / 'rasters' / reg['region']['id']
        raster_dir.mkdir(parents=True, exist_ok=True)
        prof = p.copy()
        prof.update(driver='GTiff', dtype='float32', count=1, nodata=-9999, compress='deflate', tiled=True,
                    blockxsize=256, blockysize=256)
        for name, a in ix.items():
            with rasterio.open(raster_dir / f'{name}.tif', 'w', **prof) as dst:
                dst.write(np.where(np.isfinite(a), a, -9999).astype('float32'), 1)
                dst.set_band_description(1, name)

    if site.exists():
        shutil.rmtree(site)
    (site / 'boundaries').mkdir(parents=True)
    available = write_tiles(stacks, names, site, minzoom, maxzoom)
    web = ROOT / 'src' / 'web'
    for f in ['app.js', 'style.css']:
        shutil.copy(web / f, site / f)
    version = hashlib.sha1((web / 'app.js').read_bytes() + (web / 'style.css').read_bytes()).hexdigest()[:8]
    html = (web / 'index.html').read_text(encoding='utf-8')
    html = html.replace('href="style.css"', f'href="style.css?v={version}"').replace('src="app.js"', f'src="app.js?v={version}"')
    (site / 'index.html').write_text(html, encoding='utf-8')
    shutil.copytree(ROOT / 'src' / 'vendor', site / 'vendor')

    layers = {}
    for key in LAYER_ORDER:
        if key in INDICES:
            i = INDICES[key]
            layers[key] = {'kind': 'index', 'label': key, 'name': i['full'], 'formula': i['formula'], 'about': i['about'],
                           'range': i['range'], 'low': i['low'], 'high': i['high'],
                           'lut': ''.join(f'{r:02x}{g:02x}{b:02x}' for r, g, b in lut(i))}
        else:
            c = COMPOSITES[key]
            layers[key] = {'kind': 'rgb', 'label': key, 'name': c['name'], 'formula': c['formula'], 'about': c['about']}
    site_regions = []
    for reg, res in zip(regions, results):
        p = reg['profile']
        w, s, e, n = transform_bounds(p['crs'], 'EPSG:4326', *array_bounds(p['height'], p['width'], p['transform']))
        pts = []
        for f in reg['boundaries']['features']:
            g = f['geometry']
            rings = g['coordinates'] if g['type'] == 'Polygon' else [r for poly in g['coordinates'] for r in poly]
            pts += [c for ring in rings for c in ring]
        xs, ys = [c[0] for c in pts], [c[1] for c in pts]
        (site / 'boundaries' / f"{reg['region']['id']}.geojson").write_text(
            json.dumps(reg['boundaries'], ensure_ascii=False), encoding='utf-8')
        site_regions.append({'id': reg['region']['id'], 'name': reg['region']['name'],
                             'extent': [[s, w], [n, e]], 'focus': [[min(ys), min(xs)], [max(ys), max(xs)]],
                             'stats': res['stats']})
    summary = {'product': meta['product'], 'date': meta['date'], 'minzoom': minzoom, 'maxzoom': maxzoom,
               'tiles': len(available) * len(LAYER_ORDER), 'available': available,
               'layers': layers, 'order': LAYER_ORDER, 'regions': site_regions}
    (site / 'config.json').write_text(json.dumps(summary, ensure_ascii=False), encoding='utf-8')

    summary['export_seconds'] = round(time.perf_counter() - t0, 1)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs')
    parser.add_argument('--site', type=Path, default=ROOT / 'site')
    parser.add_argument('--minzoom', type=int, default=8)
    parser.add_argument('--maxzoom', type=int, default=12)
    args = parser.parse_args()
    report = export(args.output, args.site, args.minzoom, args.maxzoom)
    for r in report['regions']:
        st = r['stats']
        print(f"{r['name']:16s} {st['area_km2']:7.1f} km²  NDBI śr. {st['indices']['NDBI']['mean']:+.3f}  "
              f"IBI śr. {st['indices']['IBI']['mean']:+.3f}  r(NDVI, NDBI) = {st['corr_ndvi_ndbi']:+.2f}")
    print(f"Kafelki: {report['tiles']}, czas: {report['export_seconds']} s")
    print(f'Podgląd: python -m http.server 8000 --directory {args.site}')


if __name__ == '__main__':
    main()
