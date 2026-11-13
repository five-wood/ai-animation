"""Isolated synthetic Biped planted-foot editability test, not source reconstruction.
Two feet are kept at the original scene pose while COM lowers by 1 cm and returns.
No source-video contact claim is made. Output is a separate demonstration scene.
"""
import json,os,math,traceback
import pymxs
rt=pymxs.runtime
BASE=r'D:\Project\ai-animation\work\max_editable_20261109'
OUT=BASE+r'\ik_support_test.max'
TRIAL=False

def xyz(v):return [float(v.x),float(v.y),float(v.z)]
def dist(a,b):return math.sqrt(sum((a[i]-b[i])**2 for i in range(3)))
def bend(a,b,c):
    v=[a[i]-b[i] for i in range(3)];w=[c[i]-b[i] for i in range(3)]
    x=sum(v[i]*w[i] for i in range(3))/math.sqrt(sum(q*q for q in v)*sum(q*q for q in w))
    return 180-math.degrees(math.acos(max(-1,min(1,x))))
def main():
    global TRIAL
    current=os.path.abspath(str(rt.maxFilePath)+str(rt.maxFileName))
    if os.path.normcase(os.path.dirname(current))!=os.path.normcase(BASE):raise RuntimeError('Not a working scene')
    if rt.getSaveRequired():raise RuntimeError('Unsaved working scene; refusing reset')
    if os.path.exists(OUT):raise RuntimeError('Output exists; refusing overwrite')
    rt.loadMaxFile(BASE+r'\base_scene.max',quiet=True);TRIAL=True
    com=rt.getNodeByName('Bip001')
    feet=[rt.getNodeByName('Bip001 '+s+' Foot') for s in ['L','R']]
    anchors=[n.transform for n in feet]
    root=xyz(com.transform.row4)
    with pymxs.attime(0):
        rt.biped.setTransform(com,rt.Name('pos'),rt.point3(*root),True)
        for foot,anchor in zip(feet,anchors):
            rt.biped.setTransform(foot,rt.Name('pos'),anchor.row4,True)
            rt.biped.setTransform(foot,rt.Name('rotation'),anchor.rotationpart,True)
            rt.biped.setSlidingKey(foot) # first independent world IK reference, no join
    for t,drop in [(30,1.),(60,0.)]:
        with pymxs.attime(t):
            rt.biped.setTransform(com,rt.Name('pos'),rt.point3(root[0],root[1],root[2]-drop),True)
            for foot,anchor in zip(feet,anchors):
                rt.biped.setTransform(foot,rt.Name('pos'),anchor.row4,True)
                rt.biped.setTransform(foot,rt.Name('rotation'),anchor.rotationpart,True)
                rt.biped.setPlantedKey(foot)
    samples=[];maxerr=[0.,0.];maxbend=[0.,0.]
    for t in range(121):
        frame=t*.5
        with pymxs.attime(frame):
            rec={'frame':frame,'com':xyz(com.transform.row4),'feet':[]}
            for i,(side,foot,anchor) in enumerate(zip(['L','R'],feet,anchors)):
                p=xyz(foot.transform.row4);ep=dist(p,xyz(anchor.row4));maxerr[i]=max(maxerr[i],ep)
                hip=xyz(rt.getNodeByName('Bip001 '+side+' Thigh').transform.row4)
                knee=xyz(rt.getNodeByName('Bip001 '+side+' Calf').transform.row4)
                theta=bend(hip,knee,p);maxbend[i]=max(maxbend[i],theta)
                rec['feet'].append({'side':side,'position':p,'anchor_error_cm':ep,'knee_flex_deg':theta,'thigh_length_cm':dist(hip,knee),'calf_length_cm':dist(knee,p)})
            samples.append(rec)
    # Simulate an artist changing only the COM depth, without touching foot keys.
    with pymxs.attime(30):
        rt.biped.setTransform(com,rt.Name('pos'),rt.point3(root[0],root[1],root[2]-1.5),True)
    after_edit=[]
    for t in range(121):
        with pymxs.attime(t*.5):
            after_edit.append([dist(xyz(foot.transform.row4),xyz(anchor.row4)) for foot,anchor in zip(feet,anchors)])
    edited_peak=[max(x[i] for x in after_edit) for i in range(2)]
    keyprops={}
    for foot in feet:
        c=foot.controller;n=int(rt.numKeys(c));keyprops[str(foot.name)]=[]
        for i in range(1,n+1):
            k=rt.biped.getKey(c,i)
            keyprops[str(foot.name)].append({str(p):str(rt.getProperty(k,p)) for p in rt.getPropNames(k)})
    status='passed_synthetic_ik_support' if max(maxerr)<.05 and max(edited_peak)<.05 and min(maxbend)>10 else 'diagnostic_not_accepted'
    rt.sliderTime=30
    if not rt.saveMaxFile(OUT,useNewFile=True,clearNeedSaveFlag=True,quiet=True):raise RuntimeError('Save failed')
    report={'status':status,'output':OUT,'synthetic':True,'source_animation_applied':False,
       'description':'Original rig test only: two feet world IK, COM lowers1cm over1s then returns by2s. Not a reconstruction of video.',
       'frames':[0,30,60],'fps':30,'max_anchor_error_cm':maxerr,'max_knee_flex_deg':maxbend,
       'com_only_edit':{'middle_com_drop_cm':1.5,'foot_keys_untouched':True,'max_anchor_error_cm':edited_peak}, 
       'biped_foot_keys':keyprops,'samples':samples,'original_scene_preserved':True}
    json.dump(report,open(BASE+r'\ik_support_report.json','w',encoding='utf8'),ensure_ascii=False,indent=2)
    print('V2R IK support test:',status,maxerr,maxbend)
try:
    main()
except Exception:
    error=traceback.format_exc()
    if TRIAL:
        try:rt.loadMaxFile(BASE+r'\base_scene.max',quiet=True)
        except:error+='\nRollback failed:\n'+traceback.format_exc()
    json.dump({'error':error},open(BASE+r'\ik_support.error.json','w'))
    raise
