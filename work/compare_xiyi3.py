import json,numpy as np,sys
sys.path.insert(0,'/data/video2rig/code')
from v2r.stabilize import temporal_metrics
root='/data/video2rig/projects'
for project,name in [('lizard','xiyi2'),('lizard','xiyi2_stable'),('lizard','xiyi3'),('lizard','xiyi3_temporal'),('lizard_clean','xiyi3_clean'),('lizard_clean','xiyi3_clean_temporal')]:
 p=root+'/'+project;r=json.load(open(p+'/rig.json'));s=json.load(open(p+'/studio.json'));m=dict(np.load(p+'/out/'+name+'/motion.npz'));rd=dict(np.load(p+'/out/'+name+'/reading.npz'))
 a=temporal_metrics(r,m,s,rd);a.pop('per_bone');print(project,name,json.dumps(a))
 for k in [22,23]:
  x=m['kp'][96:,k];print('late',r['keypoints'][k]['name'],'rangeXYZ%',np.round(np.ptp(x,axis=0)/r['size']*100,3).tolist(),'step90%',np.percentile(np.linalg.norm(np.diff(x,axis=0),axis=-1),90)/r['size']*100)
 print('UV accel median/p90',np.percentile(np.linalg.norm(np.diff(rd['uv'],n=2,axis=0),axis=-1),[50,90]))
