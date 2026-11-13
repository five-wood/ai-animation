"""One-pose native Biped behavior tests. Restores working base afterward."""
import json,math,os,traceback
import pymxs
rt=pymxs.runtime
BASE=r'D:\Project\ai-animation\work\max_editable_20261109'
def rows(m):return [[float(m[i][j]) for j in range(3)] for i in range(4)]
def mat(v):return rt.matrix3(*[rt.point3(*x) for x in v])
def main():
    p=json.load(open(BASE+r'\motion_packet.json',encoding='utf8'))
    by={b['name']:b for b in p['bones']};idx=60
    names=[b['name'] for b in p['bones'] if b['write'] and b['target_class']=='Biped_Object']+['Bip001 L Calf','Bip001 L Foot','Bip001 R Calf','Bip001 R Foot']
    out=[]
    for mode in ['rot_only','sep_tracks','com_pelvis_delta','sep_com_pelvis']:
        rt.loadMaxFile(BASE+r'\base_scene.max',quiet=True)
        nodes={name:rt.getNodeByName(name) for name in names};bc=nodes['Bip001'].controller
        if mode.startswith('sep'):
            bc.sepArmsTracks=True;bc.sepLegsTracks=True;bc.sepSpineTracks=True;bc.sepNeckTracks=True
        target={name:mat(by[name]['world_matrix3'][idx]) for name in names}
        with pymxs.attime(75):
            rt.biped.setTransform(nodes['Bip001'],rt.Name('pos'),target['Bip001'].row4,False)
            qr=target['Bip001'].rotationpart
            if 'pelvis' in mode:
                R=mat(by['Bip001 Pelvis']['reference_matrix3'])
                # Native COM carries pelvis world delta for spine attachment.
                qr=(mat(by['Bip001']['reference_matrix3'])*rt.inverse(R)*target['Bip001 Pelvis']).rotationpart
            rt.biped.setTransform(nodes['Bip001'],rt.Name('rotation'),qr,False)
            for name in names:
                if name=='Bip001':continue
                rt.biped.setTransform(nodes[name],rt.Name('rotation'),target[name].rotationpart,False)
            # Key complete pose only after applying it (shared tracks need full pose).
            rt.biped.setKey(nodes['Bip001'],True,True,True)
            for name in names:
                if name!='Bip001':rt.biped.setKey(nodes[name],copyHor=True,copyVer=True,copyTrn=True)
            out.append({'mode':mode,'sample':idx,'actual':{name:rows(nodes[name].transform) for name in names}})
    rt.loadMaxFile(BASE+r'\base_scene.max',quiet=True)
    json.dump(out,open(BASE+r'\biped_pose_probe.json','w'),indent=2)
try:
    main()
except Exception:
    open(BASE+r'\biped_pose_probe.error.txt','w').write(traceback.format_exc())
    rt.loadMaxFile(BASE+r'\base_scene.max',quiet=True)
    raise
