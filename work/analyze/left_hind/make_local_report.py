import json,csv,hashlib
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont
B=Path('work/analyze/left_hind');x=json.load(open(B/'left_hind_audit_summary.json'));r=dict(np.load(B/'temporal/reading.npz'));w=dict(np.load(B/'temporal/observation_weights.npz'));m=dict(np.load(B/'temporal/motion.npz'));rows=list(csv.DictReader(open(B/'left_hind_audit_observations.csv')));poses=list(csv.DictReader(open(B/'left_hind_audit_poses.csv')))
late=np.arange(121)>=72
extra={}
for name,k in [('hip.L',20),('knee.L',21),('ankle.L',22),('ankle.L.tip',23)]:
 for v,view in enumerate(['front','side']):
  rr=[q for q in rows if q['joint']==name and q['view']==view and int(q['frame'])>=72];e=np.array([float(q['reproj_px']) for q in rr]);ww=w['weights'][late,v,k]
  extra[name+'_'+view]={'factor_min':{f:float(w[f][late,v,k].min()) for f in ['bounds_factor','peak_factor','temporal_factor','bone_factor','ray_factor','ambiguity_factor','spread_factor']},'res_gt20_n':int((e>20).sum()),'weight_ge0.5_n':int((ww>=.5).sum()),'highweight_and_res_gt20_n':int(((ww>=.5)&(e>20)).sum()),'lowres_le5_n':int((e<=5).sum())}
  if (e>20).any():extra[name+'_'+view]['res_gt20_weight_median']=float(np.median(ww[e>20]));extra[name+'_'+view]['res_gt20_weight_range']=[float(ww[e>20].min()),float(ww[e>20].max())]
  if (ww>=.5).any():extra[name+'_'+view]['weight_ge0.5_reproj_median']=float(np.median(e[ww>=.5]));extra[name+'_'+view]['weight_ge0.5_reproj_max']=float(e[ww>=.5].max())
json.dump(extra,open(B/'extra_metrics.json','w'),indent=2)
# Simple CPU Pillow line chart, all panels use actual seconds and no inference/GT.
W,H=1500,1060;im=Image.new('RGB',(W,H),'#ffffff');d=ImageDraw.Draw(im);font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',19);title=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',26)
d.text((35,15),'Left hind, 3-5 s | observations / fit disagreement, NOT ground-truth error',fill='#18202c',font=title)
colors=['#cc5b28','#286dc0','#2c9258','#9956aa'];ts=np.arange(72,121)/24

def panel(box,heading,series,yrange):
 l,t,rr,bb=box;d.text((l,t),heading,font=font,fill='#18202c');p=(l+65,t+42,rr-20,bb-70);xmin,xmax=3,5;ymin,ymax=yrange
 for y in np.linspace(ymin,ymax,5):
  yy=p[3]-(y-ymin)/(ymax-ymin)*(p[3]-p[1]);d.line((p[0],yy,p[2],yy),fill='#e5e9ee');d.text((l,yy-10),f'{y:.2g}',font=font,fill='#697382')
 for xx in [3,3.5,4,4.5,5]:
  xxp=p[0]+(xx-xmin)/(xmax-xmin)*(p[2]-p[0]);d.text((xxp-14,p[3]+8),str(xx),font=font,fill='#697382')
 for i,(label,a) in enumerate(series):
  xy=[(p[0]+(s-xmin)/(xmax-xmin)*(p[2]-p[0]),p[3]-(float(v)-ymin)/(ymax-ymin)*(p[3]-p[1])) for s,v in zip(ts,a)]
  d.line(xy,fill=colors[i],width=3);d.text((l+10+i*175,bb-30),label,font=font,fill=colors[i])
get=lambda joint,view,key: np.array([float(q[key]) for q in rows if q['joint']==joint and q['view']==view and int(q['frame'])>=72])
panel((30,65,745,540),'ankle.L final heuristic weight (not visibility probability)',[('front',w['weights'][late,0,22]),('side',w['weights'][late,1,22]),('front temporal',w['temporal_factor'][late,0,22])],(0,1))
panel((765,65,1480,540),'ankle.L observed-to-fit reprojection distance (px)',[('front temporal',get('ankle.L','front','reproj_px')),('side temporal',get('ankle.L','side','reproj_px')),('front baseline',get('ankle.L','front','baseline_reproj_px'))],(0,35))
panel((30,560,745,1030),'knee.L local Euler X (deg), rig axes, not anatomical flexion',[('temporal',np.degrees(m['eul'][late,14,0])),('baseline',np.array([float(q['baseline_knee.L_x_deg']) for q in poses if int(q['frame'])>=72]))],(-20,30))
panel((765,560,1480,1030),'ankle.L triangulation disagreement (px)',[('front ray',w['ray_residual_px'][late,0,22]),('side ray',w['ray_residual_px'][late,1,22]),('front innovation',w['temporal_distance_px'][late,0,22])],(0,25))
im.save(B/'left_hind_evidence.png')
print(json.dumps(extra,indent=2))
