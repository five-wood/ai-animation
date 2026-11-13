"""Web orientation fix -> prepare's --up / --facing.

The web preview shows the model exactly as Blender sees it after `load_model(up="+Z")`, exported to glTF
(Y-up). glTF/three.js coordinates relate to Blender world coordinates by
    three = C @ blender,  C = [[1,0,0],[0,0,1],[0,-1,0]]   (Blender +Z -> three +Y, Blender -Y -> three +Z)
The user rotates the model in 90-degree steps until it stands on the ground and faces the front camera
(three +Z == Blender -Y, the studio's front camera side). That rotation M (3x3, three coordinates) is
an axis-aligned proper rotation; any of the 24 maps exactly onto
    up     - the model axis that must point to +Z (common.UP_ROTATIONS, applied by load_model), and
    facing - the direction the creature looks after that rotation (extract_rig, one of -Y/+Y/-X/+X),
because M_blender = Rz(yaw) @ R_up and facing = Rz(yaw)^T @ (-Y).
"""

C = ((1, 0, 0), (0, 0, 1), (0, -1, 0))
AXES = {'+X': (1, 0, 0), '-X': (-1, 0, 0), '+Y': (0, 1, 0), '-Y': (0, -1, 0), '+Z': (0, 0, 1), '-Z': (0, 0, -1)}
FACINGS = ('-Y', '+Y', '-X', '+X')


def matmul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def transpose(a):
    return tuple(tuple(a[j][i] for j in range(3)) for i in range(3))


def apply(a, v):
    return tuple(sum(a[i][k] * v[k] for k in range(3)) for i in range(3))


def det(a):
    return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
            + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))


def rot(axis, quarter_turns):
    """Right-handed rotation about a principal axis by quarter_turns * 90 degrees (integer matrix)."""
    q = quarter_turns % 4
    c, s = ((1, 0), (0, 1), (-1, 0), (0, -1))[q]
    if axis == 'X':
        return ((1, 0, 0), (0, c, -s), (0, s, c))
    if axis == 'Y':
        return ((c, 0, s), (0, 1, 0), (-s, 0, c))
    return ((c, -s, 0), (s, c, 0), (0, 0, 1))


# Same table as v2r/blender/common.UP_ROTATIONS: rotation bringing the model's `up` axis onto +Z.
UP_ROTATIONS = {'+Z': rot('X', 0), '-Z': rot('X', 2), '+Y': rot('X', 1), '-Y': rot('X', -1),
                '+X': rot('Y', -1), '-X': rot('Y', 1)}


def axis_name(v):
    for name, a in AXES.items():
        if tuple(v) == a:
            return name
    raise ValueError(f'not an axis: {v}')


def parse_matrix(value):
    """9 numbers (row-major) or 3x3 -> integer rotation matrix; ValueError unless axis-aligned proper rotation."""
    flat = [x for row in value for x in row] if value and isinstance(value[0], (list, tuple)) else list(value or [])
    if len(flat) != 9:
        raise ValueError('朝向数据格式不对')
    m = []
    for x in flat:
        if not isinstance(x, (int, float)) or isinstance(x, bool) or abs(x - round(x)) > 1e-6 or round(x) not in (-1, 0, 1):
            raise ValueError('只支持 90 度的旋转')
        m.append(int(round(x)))
    M = (tuple(m[0:3]), tuple(m[3:6]), tuple(m[6:9]))
    if matmul(M, transpose(M)) != ((1, 0, 0), (0, 1, 0), (0, 0, 1)) or det(M) != 1:
        raise ValueError('只支持 90 度的旋转')
    return M


def solve(m_three):
    """-> {'up': '+Y', 'facing': '-Y', 'matrix': [...9 ints, three coords]} for prepare --up/--facing."""
    M = parse_matrix(m_three)
    Rb = matmul(transpose(C), matmul(M, C))          # the same rotation in Blender coordinates
    up = axis_name(apply(transpose(Rb), (0, 0, 1)))   # model axis that ends up pointing to +Z
    yaw = matmul(Rb, transpose(UP_ROTATIONS[up]))     # remaining rotation, about Z only
    facing = axis_name(apply(transpose(yaw), (0, -1, 0)))
    if facing not in FACINGS:
        raise ValueError('internal: facing must be horizontal')
    return {'up': up, 'facing': facing, 'matrix': [x for row in M for x in row]}


def rebuild_blender(up, facing):
    """Inverse check: the Blender rotation that load_model(up) + a front-facing `facing` amount to."""
    for q in range(4):
        yaw = rot('Z', q)
        if apply(transpose(yaw), (0, -1, 0)) == AXES[facing]:
            return matmul(yaw, UP_ROTATIONS[up])
    raise ValueError(facing)


def all_rotations():
    out = []
    for p in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
        for sx in (1, -1):
            for sy in (1, -1):
                for sz in (1, -1):
                    m = [[0] * 3 for _ in range(3)]
                    m[0][p[0]], m[1][p[1]], m[2][p[2]] = sx, sy, sz
                    M = tuple(tuple(r) for r in m)
                    if det(M) == 1:
                        out.append(M)
    return out
