"""Build same-character native-Max target transforms from solved arrays.
System Python + NumPy only; no scene writes. Inspected frame0 is accepted only
when matched to source rest heads and corroborated by Skin bind heads.
"""
from pathlib import Path
import hashlib,json
import numpy as np
BASE=Path(__file__).resolve().parents[3]
SOURCE=BASE/'work/maxkeys/m11101'
OUT=BASE/'work/max_editable_20261109'

def qmat(q):
    q=np.asarray(q,float);q=q/np.linalg.norm(q,axis=-1,keepdims=True)
    w,x,y,z=np.moveaxis(q,-1,0)
    return np.stack([1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w),2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w),2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)],-1).reshape(q.shape[:-1]+(3,3))

def col(m):
    a=np.eye(4);a[:,:3]=m;return a.T

def rigid(m):
    a=m.copy();u,s,v=np.linalg.svd(a[:3,:3]);a[:3,:3]=u@v
    if np.linalg.det(a[:3,:3])<0:raise ValueError('reflection')
    return a


def prepare():
    r=json.loads((SOURCE/'rig.json').read_text(encoding='utf8'))
    z=np.load(SOURCE/'motion_temporal.npz',allow_pickle=False)
    profile=json.loads((BASE/'work/max_probe/bind_inspection.json').read_text(encoding='utf8'))
    bones=r['bones'];W=np.array(r['world_matrix']);M=np.array([b['rest'] for b in bones]);T=len(z['G'])
    Q=np.broadcast_to(np.eye(4),(T,len(bones),4,4)).copy();Q[:,:,:3,:3]=qmat(z['quat'])
    S=np.zeros_like(Q)
    for i,b in enumerate(bones):
        p=b['parent']
        if p<0:S[:,i]=z['G']@W@M[i]@Q[:,i]
        else:S[:,i]=S[:,p]@np.linalg.solve(M[p],M[i])@Q[:,i]
    Sr=W@M
    kps=[]
    for k in r['keypoints']:
        p=np.ones(4);p[:3]=k['local'] if k['type']=='offaxis' else [0,k.get('tail_len',bones[k['bone']]['length']) if k['type']=='tail' else 0,0]
        kps.append(np.einsum('tij,j->ti',S[:,k['bone']],p)[:,:3])
    fkerr=float(np.max(np.abs(np.stack(kps,1)-z['kp'])))
    if fkerr>1e-5:raise ValueError('FK reference mismatch')
    nodes={n['name']:n for n in profile['nodes']}
    bind={}
    for mesh in profile['binds']:
        for b in mesh['bones']:
            if isinstance(b['bind'],list):bind.setdefault(b['name'],[]).append(col(b['bind']))
    C=np.diag([100.,100.,100.,1.]) # verified centimeters by Max scene inspection/head matching
    records=[]
    for i,b in enumerate(bones):
        n=nodes[b['name']]
        ref=col(n['frame0'])
        err=float(np.linalg.norm((C@Sr[i])[:3,3]-ref[:3,3]))
        matches=[float(np.linalg.norm((C@Sr[i])[:3,3]-x[:3,3])) for x in bind.get(b['name'],[])]
        isdriver=b['active'] or b['name']=='Bip001'
        if isdriver and err>.02:raise ValueError('Source rest/current reference mismatch '+b['name'])
        if isdriver and matches and min(matches)>.02:raise ValueError('Skin bind mismatch '+b['name'])
        # Source motion delta removes imported bone axes/rest scale. Native target
        # frame0 orientation is used only after position/bind corroboration above.
        target=C@S[:,i]@np.linalg.inv(Sr[i])@np.linalg.inv(C)@ref
        if isdriver:
            target=np.stack([rigid(x) for x in target])
        # Some inactive Nub nodes intentionally have negative determinant. They
        # are reference-only and must not be coerced into writable rotations.
        records.append({'name':b['name'],'parent':b['parent'],'source_active':bool(b['active']),
            'target_class':n['class'],'target_controller':n['controller'],
            'write':bool(isdriver),'reference_head_error_cm':err,
            'skin_bind_head_errors_cm':matches,'reference_matrix3':ref.T[:,:3].tolist(),
            'world_matrix3':target.transpose(0,2,1)[:,:,:3].tolist()})
    packet={'schema':'v2r.max.motion/1','target_scene':'base_scene.max','source_motion':str(SOURCE/'motion_temporal.npz'),
        'source_sha256':hashlib.sha256((SOURCE/'motion_temporal.npz').read_bytes()).hexdigest(),
        'source_fps':float(z['fps']),'sample_count':T,'duration_seconds':(T-1)/float(z['fps']),
        'target_fps':30,'reference_frame':0,'source_to_max_scale':100.,'unit':'centimeters',
        'matrix_convention':'Max matrix3 row vector; four xyz rows',
        'max_fk_reference_error_source_units':fkerr,'contact_status':'unreviewed_no_automatic_lock',
        'protected':'all unmapped nodes and non-driver bones; existing accessory/face keys',
        'bones':records}
    OUT.mkdir(exist_ok=True,parents=True)
    path=OUT/'motion_packet.json';path.write_text(json.dumps(packet,ensure_ascii=False),encoding='utf8')
    print(json.dumps({'packet':str(path),'samples':T,'drivers':sum(x['write'] for x in records),'max_fk_error':fkerr,'max_driver_head_error_cm':max(x['reference_head_error_cm'] for x in records if x['write'])},indent=2))

if __name__=='__main__':prepare()
