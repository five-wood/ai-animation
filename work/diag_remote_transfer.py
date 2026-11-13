import bpy, os, json, hashlib, numpy as np
from mathutils import Vector
base='/data/video2rig/inspections/e6f816b193f7'
out=base+'/textured_export'
os.makedirs(out,exist_ok=True)
target='/data/video2rig/projects/m11101_nogun/out/exam/exam.fbx'
name='11101_nogun_exam_textured'
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=target,use_anim=True,anim_offset=0)
original_objects=list(bpy.context.scene.objects)
meshes=[o for o in original_objects if o.type=='MESH']
arms=[o for o in original_objects if o.type=='ARMATURE']
assert len(arms)==1 and len(meshes)==5
arm=arms[0]
frames=[1,15,30,45,60,75,90,120,150]
def geometry_snapshot():
    result={}
    for frame in frames:
        bpy.context.scene.frame_set(frame)
        dg=bpy.context.evaluated_depsgraph_get()
        result[str(frame)]={}
        for o in meshes:
            ev=o.evaluated_get(dg); me=ev.to_mesh()
            co=np.array([(ev.matrix_world@v.co)[:] for v in me.vertices],dtype=np.float64)
            result[str(frame)][o.name]=co
            ev.to_mesh_clear()
    return result
def digest_mesh(o):
    x=hashlib.sha256()
    x.update(np.array([v.co[:] for v in o.data.vertices],dtype=np.float32).tobytes())
    for v in o.data.vertices:
        for g in v.groups:x.update(f'{v.index}:{g.group}:{g.weight:.9g};'.encode())
    return x.hexdigest()
pre_digest={o.name:digest_mesh(o) for o in meshes}
pre_geometry=geometry_snapshot()
pre_bones=[(b.name,b.parent.name if b.parent else None) for b in arm.data.bones]
act=arm.animation_data.action
pre_action={'name':act.name,'range':list(act.frame_range),'curves':len(act.fcurves),'keys':sum(len(c.keyframe_points) for c in act.fcurves)}
# Save reference trajectories for fresh-import verification.
np.savez_compressed(out+'/reference_geometry.npz',**{f'{f}::{n}':co for f,d in pre_geometry.items() for n,co in d.items()})
bpy.ops.import_scene.fbx(filepath=base+'/model.fbx',use_anim=False)
source_objects=[o for o in bpy.context.scene.objects if o not in original_objects]
source_meshes={o.name:o for o in source_objects if o.type=='MESH'}
transfer=[]
for dst in meshes:
    src=source_meshes[dst.name+'_Clone']
    assert len(dst.data.vertices)==len(src.data.vertices)
    assert [l.vertex_index for l in dst.data.loops]==[l.vertex_index for l in src.data.loops]
    for i,uv in enumerate(dst.data.uv_layers):
        assert np.array_equal(np.array([x.uv[:] for x in uv.data]),np.array([x.uv[:] for x in src.data.uv_layers[i].data]))
    dst.data.materials.clear()
    for mat in src.data.materials:dst.data.materials.append(mat)
    for dp,sp in zip(dst.data.polygons,src.data.polygons):dp.material_index=sp.material_index
    # Source FBX texture nodes use the first UV set; keep it explicit and active.
    dst.data.uv_layers.active_index=0
    for i,uv in enumerate(dst.data.uv_layers):uv.active_render=(i==0)
    transfer.append({'mesh':dst.name,'source':src.name,'materials':[m.name for m in dst.data.materials]})
for o in source_objects:bpy.data.objects.remove(o,do_unlink=True)
assert pre_digest=={o.name:digest_mesh(o) for o in meshes}
post_geometry=geometry_snapshot()
transfer_geometry_error=max(float(np.abs(pre_geometry[f][n]-post_geometry[f][n]).max()) for f in pre_geometry for n in pre_geometry[f])
assert transfer_geometry_error==0
# Give packed image bytes valid, portable relative paths before FBX embeds them.
textures_dir=out+'/textures';os.makedirs(textures_dir,exist_ok=True)
image_records=[]
for img in bpy.data.images:
    if not img.has_data:continue
    file=img.name.replace('/','_').replace('\\','_')+'.png'
    img.filepath_raw=textures_dir+'/'+file;img.file_format='PNG';img.save();img.pack()
    image_records.append({'name':img.name,'size':list(img.size),'file':'textures/'+file})
sc=bpy.context.scene
sc.frame_start=int(pre_action['range'][0]);sc.frame_end=int(pre_action['range'][1]);sc.frame_set(sc.frame_start)
bpy.ops.object.select_all(action='DESELECT')
for o in original_objects:o.select_set(True)
bpy.context.view_layer.objects.active=arm
fbx=out+'/'+name+'.fbx'
bpy.ops.export_scene.fbx(filepath=fbx,use_selection=True,object_types={'ARMATURE','MESH','EMPTY'},bake_anim=True,
 bake_anim_use_all_actions=False,bake_anim_use_nla_strips=False,bake_anim_force_startend_keying=True,bake_anim_simplify_factor=0,
 add_leaf_bones=False,path_mode='COPY',embed_textures=True)
bpy.ops.wm.save_as_mainfile(filepath=out+'/'+name+'.blend',copy=True)
report={'source':base+'/model.fbx','target':target,'output':fbx,'transfers':transfer,'images':image_records,'bone_count':len(pre_bones),'bones':pre_bones,
 'original_action':pre_action,'mesh_digests':pre_digest,'transfer_geometry_max_error':transfer_geometry_error,'sampled_frames':frames}
with open(out+'/transfer_report.json','w') as f:json.dump(report,f,indent=2)
print('TEXTURE_TRANSFER_COMPLETE',json.dumps({'bone_count':len(pre_bones),'action':pre_action,'geometry_max_error':transfer_geometry_error,'fbx_bytes':os.path.getsize(fbx)}))
