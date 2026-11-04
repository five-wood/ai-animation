import json,csv,numpy as np
from pathlib import Path
p=Path(__file__).parent;z=np.load(p/'reading.npz');s=json.load(open(p/'studio.json'));r=json.load(open(p/'rig.json'));u=z['uv'];c=z['conf'];R=np.array([x['R'] for x in s['cams']]);K=np.array([x['K'] for x in s['cams']]);t=np.array([x['t'] for x in s['cams']]);C=-np.einsum('vji,vj->vi',R,t)
x=C[1]-C[0];x/=np.linalg.norm(x);forward=R[:,2].mean(0);forward-=forward@x*x;forward/=np.linalg.norm(forward);y=np.cross(forward,x);Q=np.stack([x,y,forward]);rect=[]
for v in range(2):
 ph=np.concatenate([u[:,v],np.ones(u[:,v].shape[:-1]+(1,))],-1);world=ph@np.linalg.inv(K[v]).T@R[v];rr=world@Q.T;rect.append(1500*rr[...,1]/rr[...,2])
rect=np.stack(rect,1);rows=[]
for seg,tt in {'all_overlap':np.arange(12,109),'motion31_89':np.arange(31,90),'motion31_59':np.arange(31,60),'motion60_89':np.arange(60,90)}.items():
 # Fixed confidence mask across all 25 lags prevents varying confident sample populations.
 good=c[tt,0]>.3
 for d in range(-12,13):good &= c[tt+d,1]>.3
 for ref in ['frame0','mean_first3']:
  base=rect[0] if ref=='frame0' else rect[:3].mean(0)
  for d in range(-12,13):
   err=(rect[tt,0]-base[0])-(rect[tt+d,1]-base[1])
   for group,ix in {'all':list(range(28)),'axis':[i for i,k in enumerate(r['keypoints']) if k['side']=='M'],'limbs':[i for i,k in enumerate(r['keypoints']) if k['side']!='M']}.items():
    e=np.where(good[:,ix],err[:,ix],np.nan);rows.append(dict(segment=seg,reference=ref,delay=d,group=group,rmse_px=float(np.sqrt(np.nanmean(e**2))),median_abs_px=float(np.nanmedian(abs(e))),samples=int(np.isfinite(e).sum())))
with open(p/'xiyi_independent'/'rectified_delay_scan.csv','w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
for seg in ['all_overlap','motion31_89','motion31_59','motion60_89']:
 for g in ['all','axis','limbs']:
  rs=[r for r in rows if r['segment']==seg and r['reference']=='frame0' and r['group']==g];b=min(rs,key=lambda r:r['rmse_px']);zero=[r for r in rs if r['delay']==0][0];print(seg,g,'zero',round(zero['rmse_px'],3),'best',b['delay'],round(b['rmse_px'],3),'N',b['samples'])
print('motion full curves')
for d in range(-12,13):print(d,*[round(next(r['rmse_px'] for r in rows if r['segment']=='motion31_89' and r['reference']=='frame0' and r['group']==g and r['delay']==d),2) for g in ['all','axis','limbs']])
