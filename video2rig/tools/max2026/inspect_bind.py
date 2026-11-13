"""Read-only Skin bind transforms + animation tracks for current scene."""
import json,os,traceback,hashlib
import pymxs
rt=pymxs.runtime
BASE=r'D:\Project\ai-animation'
OUT=BASE+r'\work\max_probe'

def safe(fn):
    try:return fn()
    except Exception as e:return {'error':str(e)}
def mat(m):return [[float(m[i][j]) for j in range(3)] for i in range(4)]
def track(c,path,seen):
    h=safe(lambda:int(rt.getHandleByAnim(c)))
    if not isinstance(h,int) or h in seen:return []
    seen.add(h)
    n=safe(lambda:int(rt.numKeys(c)))
    r=[{'path':path,'class':str(rt.classOf(c)),'handle':h,'num_keys':n}]
    if isinstance(n,int) and n>0:r[0]['times_ticks']=[int(rt.getKeyTime(c,i).ticks) for i in range(1,min(n,5000)+1)]
    count=safe(lambda:int(c.numSubs))
    if isinstance(count,int):
        for i in range(1,min(count,60)+1):
            sub=rt.getSubAnim(c,i)
            ctrl=safe(lambda:sub.controller)
            if ctrl is not None and not isinstance(ctrl,dict):r+=track(ctrl,path+'/'+str(rt.getSubAnimName(c,i)),seen)
    return r
try:
    source=json.load(open(BASE+r'\work\maxkeys\m11101\rig.json',encoding='utf8'))
    names={b['name'] for b in source['bones']}
    data={'file':str(rt.maxFilePath)+str(rt.maxFileName),'dirty':bool(rt.getSaveRequired()),'binds':[], 'tracks':[],'nodes':[]}
    seen=set()
    for n in list(rt.objects):
        data['tracks']+=track(n.controller,str(n.name),seen)
        if str(n.name) in names:
            with pymxs.attime(0):
                data['nodes'].append({'name':str(n.name),'class':str(rt.classOf(n)),'parent':str(n.parent.name) if n.parent else None,'frame0':mat(n.transform),'object_offset_pos':str(n.objectOffsetPos),'object_offset_rot':str(n.objectOffsetRot),'object_offset_scale':str(n.objectOffsetScale),'controller':str(rt.classOf(n.controller))})
        skins=[m for m in n.modifiers if str(rt.classOf(m))=='Skin']
        if len(skins)==1:
            rec={'mesh':str(n.name),'mesh_bind':safe(lambda:mat(rt.skinUtils.GetMeshBindTM(n))),'bones':[]}
            count=int(rt.skinOps.GetNumberBones(skins[0]))
            for i in range(1,count+1):
                bn=rt.skinOps.GetBoneName(skins[0],i,0)
                b=rt.getNodeByName(bn)
                rec['bones'].append({'name':str(bn),'bind':safe(lambda:mat(rt.skinUtils.GetBoneBindTM(n,b))),'stretch':safe(lambda:mat(rt.skinUtils.GetBoneStretchTM(n,b)))})
            data['binds'].append(rec)
    with open(OUT+r'\bind_inspection.json','w',encoding='utf8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
    print('V2R bind/animation inspection complete')
except Exception:
    open(OUT+r'\bind.error.txt','w',encoding='utf8').write(traceback.format_exc())
    raise
