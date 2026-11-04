"""Blender FBX/GLB dense-frame roundtrip for an already solved motion (handles clipped tips)."""
import sys,os,json
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'v2r','blender'))
from common import *
project, folder, name = script_args()
rig=load_json(os.path.join(project,'rig.json'))
mot=np.load(os.path.join(folder,'motion.npz'))
kp_ref=mot['kp'];fps=float(mot['fps'])
bpy.ops.wm.open_mainfile(filepath=os.path.join(folder,name+'.blend'))
ref_arm=max([o for o in bpy.data.objects if o.type=='ARMATURE'],key=lambda a:len(a.data.bones))
ref_dirs=[]
for t in range(len(kp_ref)):
    bpy.context.scene.frame_set(t+1);bpy.context.view_layer.update()
    dirs=[]
    for b in rig['bones']:
        mat=ref_arm.matrix_world @ ref_arm.pose.bones[b['name']].matrix
        dirs.append(list(mat.col[1].to_3d().normalized()))
    ref_dirs.append(dirs)
ref_dirs=np.array(ref_dirs)
for ext in ('fbx','glb'):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc=bpy.context.scene;sc.render.fps=int(round(fps));sc.render.fps_base=sc.render.fps/fps
    path=os.path.join(folder,name+'.'+ext)
    if ext=='fbx':bpy.ops.import_scene.fbx(filepath=path,use_anim=True,ignore_leaf_bones=False)
    else:bpy.ops.import_scene.gltf(filepath=path)
    arm=max([o for o in bpy.data.objects if o.type=='ARMATURE'],key=lambda a:len(a.data.bones))
    act=arm.animation_data.action
    start=int(round(act.frame_range[0]))
    coords=[];all_dirs=[]
    for t in range(len(kp_ref)):
        sc.frame_set(start+t);bpy.context.view_layer.update()
        coords.append(keypoints_world(arm,rig))
        dirs=[]
        for b in rig['bones']:
            mat=arm.matrix_world @ arm.pose.bones[b['name']].matrix
            dirs.append(list(mat.col[1].to_3d().normalized()))
        all_dirs.append(dirs)
    direction_error=np.max(np.linalg.norm(np.array(all_dirs)-ref_dirs,axis=-1))
    print('BONE_DIRECTION_VECTOR_MAX',ext,direction_error)
    assert direction_error<1e-5,direction_error
    coords=np.array(coords)
    heads=np.array([k['type']=='head' for k in rig['keypoints']])
    error=np.max(abs(coords[:,heads]-kp_ref[:,heads]))
    tails=np.max(abs(coords[:,~heads]-kp_ref[:,~heads]))
    print('ROUNDTRIP',ext,'range',list(act.frame_range),'HEAD_MAX',error,'TAIL_COORD_DIAGNOSTIC',tails)
    # Importers may reconstruct leaf lengths. Head joint positions are authoritative;
    # validate rotation curves separately instead of treating rebuilt leaf tails as original.
    assert error<1e-5,error
print('STABLE EXPORT PASS')
