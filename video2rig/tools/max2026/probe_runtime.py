import json,os,hashlib
import pymxs
rt=pymxs.runtime
n=rt.getNodeByName('Bone001')
data={'controller':str(rt.classOf(n.controller)),'property_rotation_controller':str(rt.classOf(rt.getPropertyController(n,rt.Name('rotation')))),'source_sha':hashlib.sha256(open(r'D:\Project\ai-animation\video2rig\tools\max2026\import_motion.py','rb').read()).hexdigest(),'time':str(rt.sliderTime),'file':str(rt.maxFilePath)+str(rt.maxFileName)}
open(r'D:\Project\ai-animation\work\max_editable_20261109\runtime_probe.json','w').write(json.dumps(data,indent=2))
print(data)
