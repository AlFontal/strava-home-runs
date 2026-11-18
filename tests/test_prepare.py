import json
from itertools import permutations, product

import numpy as np
from PIL import Image

from homeruns import prepare

A, B, C, D = (0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)


def geom(*pts):
    return [{"lat": la, "lon": lo} for la, lo in pts]


def edges(ring):
    return {frozenset(e) for e in zip(ring, ring[1:])}


def test_join_rings_closes_a_ring_from_ways_in_any_order_and_direction():
    sides = [[A, B], [B, C], [C, D], [D, A]]
    for order in permutations(sides):
        for flip in product((False, True), repeat=4):
            rings = prepare.join_rings([s[::-1] if f else s for s, f in zip(order, flip)])
            assert len(rings) == 1
            assert rings[0][0] == rings[0][-1] and edges(rings[0]) == edges([A, B, C, D, A])


def test_join_rings_drops_chains_that_cannot_close():
    island = [(0.4, 0.4), (0.4, 0.6), (0.6, 0.5), (0.4, 0.4)]
    assert prepare.join_rings([[A, B], [B, C], [C, D]]) == []        # a member is missing
    assert prepare.join_rings([[A, B], island, [B, C]]) == [island]


def test_water_areas_reads_closed_ways_and_multipolygons_with_islands():
    i1, i2, i3 = (0.4, 0.4), (0.4, 0.6), (0.6, 0.5)
    elements = [
        {"type": "way", "tags": {"natural": "water"}, "geometry": geom(A, B, C, A)},
        {"type": "way", "tags": {"highway": "residential"}, "geometry": geom(A, B, C, A)},
        {"type": "relation", "tags": {"type": "multipolygon", "natural": "water", "water": "lake"}, "members": [
            {"type": "way", "role": "outer", "geometry": geom(A, B, C)},
            {"type": "way", "role": "outer", "geometry": geom(C, D, A)},
            {"type": "way", "role": "inner", "geometry": geom(i1, i2, i3, i1)},
            {"type": "node", "role": "label"},
        ]},
    ]
    areas = prepare.water_areas(elements)
    assert len(areas) == 2
    assert areas[0] == ([[A, B, C, A]], [])
    outer, inner = areas[1]
    assert len(outer) == 1 and len(outer[0]) == 5
    assert inner == [[i1, i2, i3, i1]]


def box(s, n, w, e):
    return [(s, w), (s, e), (n, e), (n, w), (s, w)]


def test_prepare_draws_lakes_with_islands_and_ponds_as_water_and_keeps_bridges_out(tmp_path):
    S, N, W, E = 38.700, 38.710, -9.150, -9.140                     # ~1 km in Lisbon, no sea
    lake = box(38.702, 38.708, -9.148, -9.142)
    island = box(38.7045, 38.7055, -9.1460, -9.1450)
    pond = box(38.7047, 38.7051, -9.1458, -9.1455)                  # on the island, mapped on its own
    pond2 = box(38.7047, 38.7051, -9.1454, -9.1451)                 # on the island, another outer ring of the lake
    bridge = [(38.706, -9.150), (38.706, -9.140)]
    (tmp_path / "bbox.json").write_text(json.dumps(dict(S=S, N=N, W=W, E=E)))
    np.save(tmp_path / "dem.npy", np.full((20, 20), 50.0, np.float32))
    (tmp_path / "streets.json").write_text(json.dumps({"elements": [   # Overpass lists ways before relations
        {"type": "way", "tags": {"natural": "water"}, "geometry": geom(*pond)},
        {"type": "way", "tags": {"highway": "secondary"}, "geometry": geom(*bridge)},
        {"type": "relation", "tags": {"natural": "water"}, "members": [
            {"type": "way", "role": "outer", "geometry": geom(*lake)},
            {"type": "way", "role": "inner", "geometry": geom(*island)},
            {"type": "way", "role": "outer", "geometry": geom(*pond2)}]},
    ]}))
    prepare.prepare(tmp_path, tmp_path, [], log=lambda *a: None)
    tex = np.asarray(Image.open(tmp_path / "streets.png"))
    h, w = tex.shape[:2]

    def at(lat, lon):
        return tex[int((N - lat) / (N - S) * h), int((lon - W) / (E - W) * w)]

    assert at(38.703, -9.145)[2] == 255                              # lake
    assert at(38.7053, -9.1455)[2] == 0                              # island
    assert at(38.7049, -9.14565)[2] == 255                           # pond, drawn after the island
    assert at(38.7049, -9.14525)[2] == 255                           # pond2, drawn after the island
    assert at(38.7049, -9.1458)[0] == 0                              # the pond's outline isn't drawn as a path
    assert at(38.701, -9.145)[2] == 0                                # land
    assert at(38.706, -9.145)[0] > 0 and at(38.706, -9.145)[2] == 0  # bridge: a street, not water
