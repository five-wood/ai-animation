"""Create a non-overwriting working .max copy, preserving the original file."""
import json,os,hashlib,traceback
import pymxs
rt=pymxs.runtime
BASE=r'D:\Project\ai-animation'
OUT=BASE+r'\work\max_editable_20261109'
os.makedirs(OUT,exist_ok=True)
source=BASE+r'\11101_01(10.22).max'
target=OUT+r'\base_scene.max'
try:
    current=os.path.normcase(os.path.abspath(str(rt.maxFilePath)+str(rt.maxFileName)))
    if current!=os.path.normcase(source):raise RuntimeError('Unexpected scene; refusing copy: '+current)
    if bool(rt.getSaveRequired()):raise RuntimeError('Scene has unsaved changes; refusing automatic processing')
    if os.path.exists(target):raise RuntimeError('Working copy already exists; refusing overwrite')
    digest=hashlib.sha256(open(source,'rb').read()).hexdigest()
    ok=rt.saveMaxFile(target,useNewFile=True,clearNeedSaveFlag=True,quiet=True)
    if not ok:raise RuntimeError('Max save returned false')
    if hashlib.sha256(open(source,'rb').read()).hexdigest()!=digest:raise RuntimeError('Original hash changed')
    with open(OUT+r'\working_copy.json','w',encoding='utf8') as f:
        json.dump({'source':source,'sha256':digest,'working_copy':target,'current_file':str(rt.maxFilePath)+str(rt.maxFileName),'max_version':str(rt.maxVersion()),'original_preserved':True},f,ensure_ascii=False,indent=2)
    print('V2R working copy ready:',target)
except Exception:
    open(OUT+r'\copy.error.txt','w',encoding='utf8').write(traceback.format_exc())
    raise
