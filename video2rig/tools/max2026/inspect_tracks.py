"""Detailed read-only controller/animation probe. Run in the existing Max main thread."""
import json, os, traceback
import pymxs
rt=pymxs.runtime
OUT=r'D:\Project\ai-animation\work\max_probe'

def val(fn):
    try:return fn()
    except Exception as e:return {'error':str(e)}
def props(x):
    return {str(p):val(lambda p=p:str(rt.getProperty(x,p))) for p in rt.getPropNames(x)}
def subtrack(c,level=0,seen=None):
    seen=set() if seen is None else seen
    h=val(lambda:int(rt.getHandleByAnim(c)))
    if isinstance(h,int):
        if h in seen:return {'ref':h}
        seen.add(h)
    n=val(lambda:int(rt.numKeys(c)))
    result={'class':str(rt.classOf(c)),'handle':h,'num_keys':n}
    if isinstance(n,int) and n>0:
        result['times_ticks']=[val(lambda i=i:int(rt.getKeyTime(c,i).ticks)) for i in range(1,min(n,2000)+1)]
        result['first_key']=val(lambda:props(rt.biped.getKey(c,1))) if 'Bip' in result['class'] or result['class'].startswith(('Vertical','Horizontal','Turning')) else val(lambda:props(rt.getKey(c,1)))
    if level>=5:return result
    count=val(lambda:int(rt.getNumSubAnims(c)))
    if isinstance(count,dict):count=val(lambda:int(c.numSubs))
    result['num_subanims']=count
    if isinstance(count,int):
        children=[]
        for i in range(1,min(count,50)+1):
            s=val(lambda i=i:rt.getSubAnim(c,i))
            ctl=val(lambda:rt.getProperty(s,rt.Name('controller')))
            if ctl is None or isinstance(ctl,dict):
                children.append({'index':i,'name':val(lambda i=i:str(rt.getSubAnimName(c,i))),'controller_error':ctl})
            else:
                children.append({'index':i,'name':val(lambda i=i:str(rt.getSubAnimName(c,i))),'track':subtrack(ctl,level+1,seen)})
        result['children']=children
    return result
try:
    results={'file':str(rt.maxFilePath)+str(rt.maxFileName),'nodes':[]}
    names=['root','Bip001','Bip001 Pelvis','Bip001 Spine','Bip001 Spine1','Bip001 L Thigh','Bip001 L Calf','Bip001 L Foot','Bip001 R Thigh','Bip001 R Foot','Bip001 L Clavicle','Bip001 Head','Bip001_L_UpperArm_Bone','Bone001','Twist_L_Forearm_Bone']
    for name in names:
        node=rt.getNodeByName(name)
        if node:
            results['nodes'].append({'name':name,'tracks':subtrack(node.controller),'properties':props(node.controller)})
    stream=rt.StringStream('')
    rt.showInterface(rt.skinUtils,to=stream)
    results['skinUtils_interface']=str(stream)
    results['skinOps_members']=val(lambda:[str(x) for x in rt.getPropNames(rt.skinOps)])
    results['scene_dirty']=val(lambda:bool(rt.getSaveRequired()))
    results['skin_functions']={name:val(lambda name=name:str(getattr(rt.skinUtils,name))) for name in ['GetBoneBindTM','GetBoneInitialTM','GetSkinInitTM']}
    with open(os.path.join(OUT,'tracks_inspection.json'),'w',encoding='utf8') as f:json.dump(results,f,ensure_ascii=False,indent=2)
except Exception:
    with open(os.path.join(OUT,'tracks.error.txt'),'w',encoding='utf8') as f:f.write(traceback.format_exc())
    raise
