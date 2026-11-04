from pathlib import Path
import json, numpy as np
base=Path('/data/video2rig')
print('CODE FILES')
print('\n'.join(str(p) for p in (base/'code').rglob('*.py')))
for name in ['lizard','lizard_clean']:
 p=base/'projects'/name
 print('\nRIG',name,(p/'rig.json').read_text())
 for f in sorted((p/'out').glob('*/reading.npz')):
  a=np.load(f,allow_pickle=True)
  print('NPZ',str(f),{k:(a[k].shape,str(a[k].dtype)) for k in a.files})
 for f in sorted((p/'exam/frames').glob('*.json')):
  d=json.loads(f.read_text()); print('JSON',str(f),str(d)[:14000])
