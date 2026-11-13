import bpy, os, json, numpy as np
from mathutils import Vector
base='/data/video2rig/inspections/e6f816b193f7/textured_export'
name='11101_nogun_exam_textured'
with open(base+'/transfer_report.json') as f:report=json.load(f)
ref=np.load(base+'/reference_geometry.npz')
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=base+'/'+name+'.fbx',use_anim=True,anim_offset=0)
sc=bpy.context.scene
meshes=[o for o in sc.objects if o.type=='MESH'];arms=[o for o in sc.objects if o.type=='ARMATURE']
assert len(meshes)==5 and len(arms)==1
arm=arms[0]
bones=[(b.name,b.parent.name if b.parent else None) for b in arm.data.bones]
assert bones==[tuple(x) for x in report['bones']]
act=arm.animation_data.action
assert act is not None
assert list(act.frame_range)==report['original_action']['range']
errors={}
for frame in report['sampled_frames']:
    sc.frame_set(frame);dg=bpy.context.evaluated_depsgraph_get()
    errors[str(frame)]={}
    for o in meshes:
        ev=o.evaluated_get(dg);me=ev.to_mesh()
        co=np.array([(ev.matrix_world@v.co)[:] for v in me.vertices],dtype=np.float64);ev.to_mesh_clear()
        original=ref[f'{frame}::{o.name}']
        assert co.shape==original.shape
        errors[str(frame)][o.name]={'max_abs_error':float(np.abs(co-original).max()),'rms_error':float(np.sqrt(np.mean((co-original)**2)))}
max_error=max(x['max_abs_error'] for d in errors.values() for x in d.values())
with open(base+'/geometry_debug.json','w') as f:json.dump({'max_error':max_error,'errors':errors,'fps':sc.render.fps/sc.render.fps_base,'action_range':list(act.frame_range)},f,indent=2)
assert max_error<0.0001, max_error
mesh_data=[]
for o in meshes:
    assert any(m.type=='ARMATURE' and m.object==arm for m in o.modifiers)
    mesh_data.append({'name':o.name,'vertices':len(o.data.vertices),'vertex_groups':len(o.vertex_groups),'materials':[m.name for m in o.data.materials],'uv_layers':[u.name for u in o.data.uv_layers]})
images=[{'name':i.name,'size':list(i.size),'packed':bool(i.packed_file),'has_data':i.has_data} for i in bpy.data.images]
assert len(images)==len(report['images']) and all(i['has_data'] and i['packed'] for i in images)
result={'passed':True,'bone_count':len(bones),'hierarchy_unchanged':True,'action':{'name':act.name,'range':list(act.frame_range),'curves':len(act.fcurves),'keys':sum(len(fc.keyframe_points) for fc in act.fcurves)},'geometry_max_error':max_error,'geometry_errors':errors,'meshes':mesh_data,'images':images,'fps':sc.render.fps/sc.render.fps_base}
with open(base+'/validation.json','w') as f:json.dump(result,f,indent=2)
print('VALIDATION_PASSED',max_error, flush=True)
# Fresh import renders demonstrate self-contained FBX texture loading and animation.
sc.render.engine='BLENDER_EEVEE';sc.eevee.taa_render_samples=64;sc.eevee.use_gtao=True;sc.eevee.gtao_distance=.15
sc.render.resolution_x=960;sc.render.resolution_y=960;sc.render.resolution_percentage=100;sc.render.image_settings.file_format='PNG'
sc.world=bpy.data.worlds.new('PreviewWorld');sc.world.use_nodes=True
sc.world.node_tree.nodes['Background'].inputs['Color'].default_value=(.2,.2,.2,1);sc.world.node_tree.nodes['Background'].inputs['Strength'].default_value=.6
sc.view_settings.view_transform='Standard';sc.view_settings.look='Medium High Contrast'
sc.frame_set(1)
pts=[o.matrix_world@Vector(c) for o in meshes for c in o.bound_box]
lo=Vector([min(p[i] for p in pts) for i in range(3)]);hi=Vector([max(p[i] for p in pts) for i in range(3)])
center=(lo+hi)*.5;extent=max(hi-lo)
for name,offset,power,size in [('Key',(2,-3,4),1300,3),('Fill',(-3,-1,2),800,3),('Rim',(1,3,3),1100,2)]:
    data=bpy.data.lights.new(name,'AREA');data.energy=power*(extent/2)**2;data.shape='DISK';data.size=size*extent/2
    ob=bpy.data.objects.new(name,data);sc.collection.objects.link(ob);ob.location=center+Vector(offset)*extent;ob.rotation_euler=(center-ob.location).to_track_quat('-Z','Y').to_euler()
camdata=bpy.data.cameras.new('PreviewCamera');cam=bpy.data.objects.new('PreviewCamera',camdata);sc.collection.objects.link(cam);sc.camera=cam
camdata.type='ORTHO';camdata.ortho_scale=extent*1.35;camdata.clip_end=100
for frame,view,direction in [(1,'front',(0,-1,.04)),(1,'threequarter',(1,-2,.6)),(30,'action',(1,-2,.6)),(60,'action',(1,-2,.6)),(90,'action',(1,-2,.6))]:
    sc.frame_set(frame)
    # Track the posed bounding box for action preview only.
    pts=[o.matrix_world@Vector(c) for o in meshes for c in o.bound_box]
    lo=Vector([min(p[i] for p in pts) for i in range(3)]);hi=Vector([max(p[i] for p in pts) for i in range(3)])
    aim=(lo+hi)*.5
    cam.location=aim+Vector(direction).normalized()*extent*4;cam.rotation_euler=(aim-cam.location).to_track_quat('-Z','Y').to_euler()
    sc.render.filepath=base+f'/preview_{frame:03d}_{view}.png';bpy.ops.render.render(write_still=True)
print('FRESH_IMPORT_PREVIEWS_COMPLETE',flush=True)
