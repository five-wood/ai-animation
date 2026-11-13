"""Isolated controller tests in the working copy, restored after each test."""
import json,os,traceback
import pymxs
rt=pymxs.runtime
BASE=r'D:\Project\ai-animation\work\max_editable_20261109'
def rows(m):return [[float(m[i][j]) for j in range(3)] for i in range(4)]
def mat(v):return rt.matrix3(*[rt.point3(*x) for x in v])
def q(q):return [float(q.x),float(q.y),float(q.z),float(q.w)]
def snap(n):return {'tm':rows(n.transform),'node_rotation':q(n.rotation),'matrix_rotation':q(n.transform.rotationpart)}
try:
    current=os.path.abspath(str(rt.maxFilePath)+str(rt.maxFileName))
    if os.path.normcase(os.path.dirname(current))!=os.path.normcase(BASE):raise RuntimeError('Wrong scene folder')
    p=json.load(open(BASE+r'\motion_packet.json',encoding='utf8'));b=next(x for x in p['bones'] if x['name']=='Bone001');tm=mat(b['world_matrix3'][0])
    out={'wanted':b['world_matrix3'][0],'tests':[]}
    rt.execute('fn v2rSetWorldRotation node q = (in coordsys (transMatrix node.transform.pos) (node.rotation = q))')
    for name in ['direct','inverse','transform']:
        rt.loadMaxFile(BASE+r'\base_scene.max',quiet=True)
        n=rt.getNodeByName('Bone001')
        rec={'mode':name,'before':snap(n),'target_quat':q(tm.rotationpart)}
        if name=='direct':rt.v2rSetWorldRotation(n,tm.rotationpart)
        if name=='inverse':rt.v2rSetWorldRotation(n,rt.inverse(tm.rotationpart))
        if name=='transform':
            v=n.transform
            for i in range(3):v[i]=tm[i]
            n.transform=v
        rec['after']=snap(n);out['tests'].append(rec)
    rt.loadMaxFile(BASE+r'\dense_reference_diagnostic.max',quiet=True)
    out['dense_keys']={}
    for name in ['Bip001 L Clavicle','Bip001 L Hand','Bip001 L Foot','Bip001 Spine']:
        n=rt.getNodeByName(name);ctl=n.controller
        keys=[]
        for i in [1,min(2,int(rt.numKeys(ctl)))]:
            if i<=0:continue
            k=rt.biped.getKey(ctl,i)
            keys.append({str(prop):str(rt.getProperty(k,prop)) for prop in rt.getPropNames(k)})
        out['dense_keys'][name]=keys
    rt.loadMaxFile(BASE+r'\base_scene.max',quiet=True)
    json.dump(out,open(BASE+r'\pose_probe.json','w',encoding='utf8'),indent=2)
except Exception:
    json.dump({'error':traceback.format_exc()},open(BASE+r'\pose_probe.error.json','w'))
    raise
