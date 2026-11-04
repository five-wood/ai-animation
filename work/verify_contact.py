import sys,json,numpy as np,pathlib
sys.path.insert(0,'/data/video2rig/code')
from v2r.geom import Skeleton
p=pathlib.Path('/data/video2rig/projects/lizard');rig=json.loads((p/'rig.json').read_text()); sk=Skeleton(rig)
a=dict(np.load(p/'out/xiyi2_stable/motion.npz'));b=dict(np.load(p/'out/xiyi2_contact/motion.npz'))
r=json.loads((p/'out/xiyi2_contact/contact.json').read_text())
changed=[i for i,bi in enumerate(sk.active) if rig['bones'][bi]['name'] in r['modified_bones']]
unchanged=[i for i in range(sk.A) if i not in changed]
assert np.array_equal(a['G'],b['G'])
assert np.array_equal(a['eul'][:,unchanged],b['eul'][:,unchanged])
assert np.array_equal(a['eul'][:73],b['eul'][:73])
assert np.all(b['eul']>=sk.limits[sk.active,:,0]-1e-6) and np.all(b['eul']<=sk.limits[sk.active,:,1]+1e-6)
bl=np.array(json.loads((p/'out/xiyi2_contact/blender_kp3d.json').read_text()))
assert abs(bl-b['kp']).max()<1e-5
print('root and other local joints unchanged; before 3s unchanged; limits PASS; export max',abs(bl-b['kp']).max())
for n in ('before_contact','after_contact'):print(n,r[n])
for n in ('before','after'):print(n,'reproj',r[n]['reprojection_median_px'],r[n]['reprojection_p90_px'],'height',r[n]['root_height_range'],'lowtime',r[n]['low_hold_centre_seconds'])
# Show frame step near fade boundaries and maximum angles; no unobserved perfection claims.
ki=next(i for i,k in enumerate(rig['keypoints']) if k['name']=='ankle.L.tip')
for lo,hi in ((70,77),(81,89),(96,121)):
 delta=np.linalg.norm(np.diff(b['kp'][lo:hi,ki],axis=0),axis=-1)/sk.size*100
 print('toe step pct size',lo,hi,'max',float(delta.max()))
