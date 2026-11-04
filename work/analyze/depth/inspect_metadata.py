from pathlib import Path
import json,numpy as np
for name in ['lizard','lizard_clean']:
 p=Path('/data/video2rig/projects')/name
 studio=json.loads((p/'studio.json').read_text()); print('STUDIO',name,json.dumps(studio))
 print('MODEL',name,[str(f) for f in (p/'model').glob('*')])
 for f in sorted((p/'out').glob('*/reading.npz')):
  a=np.load(f); o=f.parent
  s=json.loads((o/'summary.json').read_text()) if (o/'summary.json').exists() else {}
  c=json.loads((o/'check.json').read_text()) if (o/'check.json').exists() else {}
  print('RUN',name,o.name,'solver',s.get('solver'),'swaps',c.get('label_swaps_fixed'),'source',s.get('source_video'),'delta_raw',float(abs(a['uv']-a['uv_raw']).max()),'conf',np.percentile(a['conf'],[0,10,50,90,100]).tolist(),'conf_fit',np.percentile(a['conf_fit'],[0,10,50,90,100]).tolist() if 'conf_fit' in a else None,'depth',np.percentile(a['depth'],[0,10,50,90,100]).tolist())
