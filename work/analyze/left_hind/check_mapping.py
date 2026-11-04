import numpy as np,json
from pathlib import Path
b=Path('work/analyze/left_hind');r=np.load(b/'temporal/reading.npz');z={}
for k in [20,21,22,23]:
 mirror=k+4;d=np.linalg.norm(r['uv'][:,:,k]-r['uv_raw'][:,:,k],axis=-1);dm=np.linalg.norm(r['uv'][:,:,k]-r['uv_raw'][:,:,mirror],axis=-1)
 z[k]={'swap_frames_front':np.flatnonzero(d[:,0]>.01).tolist(),'swap_frames_side':np.flatnonzero(d[:,1]>.01).tolist(),'reader_to_raw_median':np.median(np.linalg.norm(r['uv_reader'][:,:,k]-r['uv_raw'][:,:,k],axis=-1),axis=0).tolist(),'max_uv_vs_raw':d.max(axis=0).tolist(),'swap_exact_mirror_max_diff':float(dm[d>.01].max()) if (d>.01).any() else None,'samples':{str(t):{key:r[key][t,:,k].tolist() for key in ['uv_reader','uv_raw','uv']} for t in [72,84,90,96,108,120]}}
print(json.dumps(z,indent=2));json.dump(z,open(b/'mapping_check.json','w'),indent=2)
