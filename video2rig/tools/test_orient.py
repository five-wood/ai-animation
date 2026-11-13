"""Unit tests for studio/orient.py (web 90-degree rotation -> prepare --up/--facing). Stdlib only.
python video2rig/tools/test_orient.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from studio import orient as O  # noqa: E402

n = 0


def check(cond, msg):
    global n
    if not cond:
        raise SystemExit('FAIL: ' + msg)
    n += 1


# UP_ROTATIONS really bring each axis onto +Z (same convention as v2r/blender/common.py)
for name, R in O.UP_ROTATIONS.items():
    check(O.apply(R, O.AXES[name]) == (0, 0, 1), f'UP_ROTATIONS[{name}] maps {name} to +Z')
# C maps Blender up/front onto three.js up/front
check(O.apply(O.C, (0, 0, 1)) == (0, 1, 0), 'Blender +Z -> three +Y')
check(O.apply(O.C, (0, -1, 0)) == (0, 0, 1), 'Blender -Y (front camera side) -> three +Z')

# identity: an upright, front-facing model keeps the defaults
check(O.solve([1, 0, 0, 0, 1, 0, 0, 0, 1]) == {'up': '+Z', 'facing': '-Y', 'matrix': [1, 0, 0, 0, 1, 0, 0, 0, 1]}, 'identity')

# every one of the 24 rotations round-trips exactly and the 24 (up, facing) pairs are distinct
seen = set()
rots = O.all_rotations()
check(len(rots) == 24, '24 rotations')
for M in rots:
    r = O.solve(M)
    Rb = O.matmul(O.transpose(O.C), O.matmul(M, O.C))
    check(O.rebuild_blender(r['up'], r['facing']) == Rb, f'round trip {M} -> {r}')
    check(r['facing'] in O.FACINGS, 'facing is horizontal')
    seen.add((r['up'], r['facing']))
check(len(seen) == 24, 'all 24 (up, facing) pairs reachable')

# concrete cases used by the server test
# A model lying on its back, head toward Blender +Y: the user tips it forward once (three Rx(+90))
check(O.solve(O.rot('X', 1)) == {'up': '+Y', 'facing': '-Y', 'matrix': [x for row in O.rot('X', 1) for x in row]}, 'lying on back')
# lying on its back with head toward Blender -X, face up: fix = Blender Rx(90) Rz(-90)
fix_b = O.matmul(O.rot('X', 1), O.rot('Z', -1))
M = O.matmul(O.C, O.matmul(fix_b, O.transpose(O.C)))
r = O.solve(M)
check((r['up'], r['facing']) == ('-X', '+X'), f'lying sideways -> {r}')
# a standing model that faces away from the camera: turn it 180 degrees about the vertical axis
r = O.solve(O.rot('Y', 2))
check((r['up'], r['facing']) == ('+Z', '+Y'), f'facing away -> {r}')

# invalid input
for bad in ([1, 0, 0, 0, 1, 0, 0, 0, -1], [0.7, 0.7, 0, 0, 1, 0, 0, 0, 1], [1, 0, 0], 'x', [True] * 9):
    try:
        O.solve(bad)
        check(False, f'rejects {bad}')
    except (ValueError, TypeError):
        check(True, 'rejected')
print(f'test_orient: {n} checks passed')
