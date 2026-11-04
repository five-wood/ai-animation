import json,numpy as np,sys,torch,time
sys.path.insert(0,'/data/video2rig/code');torch.set_num_threads(2)
from v2r.solve import fit
from v2r.solve_temporal import fit_temporal
from v2r.stabilize import continuous_quats
from tools.benchmark_depth import evaluate
root='/data/video2rig';out=root+'/experiments/depth_order_20261103';rows=[]
for proj,clip in [('lizard','exam_temporal'),('lizard','xiyi2_temporal_web')]:
 p=root+'/projects/'+proj;r=json.load(open(p+'/rig.json'));st=json.load(open(p+'/studio.json'))
 rd=dict(np.load(p+'/out/'+clip+'/reading.npz'));fps=float(np.load(p+'/out/'+clip+'/motion.npz')['fps'])
 gt=json.load(open(p+'/exam/frames/motion_gt.json')) if clip.startswith('exam') else None
 init=fit(r,st,rd['uv'],rd['conf_fit'],rd['X_fit'],depth=None,iters=900,verbose=False,device='cuda',fps=fps)
 for mode in ['numeric','order']:
  for strength in [.25,2.5]:
   s=fit_temporal(r,st,rd['uv'],rd['conf_fit'],rd['X_fit'],fps,depth=rd['depth'],initial=init,depth_mode=mode,depth_strength=strength,verbose=False)
   sk=s['skeleton'];m=dict(G=s['G'],eul=s['eul'],kp=s['kp'],quat=continuous_quats(sk.local_quats(s['eul'])),fps=fps)
   a=evaluate(r,st,m,rd,gt);a.update(project=proj,clip=clip,mode=mode,strength=strength)
   rows.append(a);print(json.dumps(a),flush=True)
   np.savez(out+'/'+clip+'_'+mode+'_'+str(strength)+'.npz',**m)
json.dump(rows,open(out+'/sensitivity.json','w'),indent=1)
