import bpy, os, json, math, sys
from mathutils import Vector
from collections import Counter
base = '/data/video2rig/inspections/e6f816b193f7'
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=base+'/model.fbx', use_anim=True)
sc = bpy.context.scene
meshes = [o for o in sc.objects if o.type == 'MESH']
arms = [o for o in sc.objects if o.type == 'ARMATURE']
def vec(v): return [round(float(x),6) for x in v]
def bbox(objects):
    pts = [o.matrix_world @ Vector(c) for o in objects for c in o.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    return lo, hi
report = {'file_bytes':os.path.getsize(base+'/model.fbx'), 'objects':dict(Counter(o.type for o in sc.objects)),
          'scene_units':{'system':sc.unit_settings.system,'scale_length':sc.unit_settings.scale_length},
          'fps':sc.render.fps/sc.render.fps_base, 'meshes':[], 'armatures':[], 'materials':[], 'images':[], 'actions':[]}
for o in meshes:
    d=o.data; d.calc_loop_triangles()
    bone_names = set()
    for mod in o.modifiers:
        if mod.type=='ARMATURE' and mod.object: bone_names.update(mod.object.data.bones.keys())
    groups = {g.index:g.name for g in o.vertex_groups}
    influences=[]; unweighted=0; bad_sums=0
    for v in d.vertices:
        weights=[g.weight for g in v.groups if groups.get(g.group) in bone_names and g.weight>1e-6]
        influences.append(len(weights))
        if not weights: unweighted+=1
        elif abs(sum(weights)-1)>0.01: bad_sums+=1
    report['meshes'].append({'name':o.name, 'vertices':len(d.vertices), 'polygons':len(d.polygons), 'triangles':len(d.loop_triangles),
       'dimensions':vec(o.dimensions), 'location':vec(o.location),'rotation_degrees':vec([math.degrees(x) for x in o.rotation_euler]),'scale':vec(o.scale),
       'parent':o.parent.name if o.parent else None, 'parent_type':o.parent_type, 'parent_bone':o.parent_bone,
       'modifiers':[{'name':m.name,'type':m.type,'armature':m.object.name if m.type=='ARMATURE' and m.object else None} for m in o.modifiers],
       'vertex_group_count':len(o.vertex_groups),'vertex_groups':list(groups.values()),'unweighted_vertices_to_modifiers':unweighted,
       'weight_sum_deviations':bad_sums,'max_influences':max(influences,default=0),'influence_histogram':dict(Counter(influences)),
       'materials':[m.name if m else None for m in d.materials], 'uv_layers':[u.name for u in d.uv_layers],
       'shape_keys':list(d.shape_keys.key_blocks.keys()) if d.shape_keys else [], 'hidden_render':o.hide_render})
for o in arms:
    report['armatures'].append({'name':o.name,'bone_count':len(o.data.bones),'deform_bone_count':sum(b.use_deform for b in o.data.bones),
      'roots':[b.name for b in o.data.bones if b.parent is None], 'scale':vec(o.scale),
      'active_action':o.animation_data.action.name if o.animation_data and o.animation_data.action else None,
      'bones':[{'name':b.name,'parent':b.parent.name if b.parent else None,'deform':b.use_deform,'head':vec(o.matrix_world@b.head_local),'tail':vec(o.matrix_world@b.tail_local), 'constraints':[c.type for c in o.pose.bones[b.name].constraints]} for b in o.data.bones]})
for m in bpy.data.materials:
    nodes=[]
    if m.use_nodes:
        for n in m.node_tree.nodes:
            entry={'name':n.name,'type':n.type}
            if n.type=='TEX_IMAGE': entry['image']=n.image.name if n.image else None
            if n.type=='BSDF_PRINCIPLED':
                entry['inputs']={k: {'linked':n.inputs[k].is_linked, 'value':list(n.inputs[k].default_value) if hasattr(n.inputs[k].default_value,'__len__') else n.inputs[k].default_value} for k in ['Base Color','Metallic','Roughness','Alpha']}
            nodes.append(entry)
    report['materials'].append({'name':m.name,'diffuse_color':list(m.diffuse_color),'nodes':nodes})
for i in bpy.data.images:
    path=bpy.path.abspath(i.filepath)
    report['images'].append({'name':i.name,'size':list(i.size),'channels':i.channels,'packed':bool(i.packed_file),'source':i.source,'filepath':i.filepath,'exists':os.path.isfile(path),'has_data':i.has_data})
for a in bpy.data.actions:
    report['actions'].append({'name':a.name,'frame_range':list(a.frame_range),'fcurves':len(a.fcurves),'keys':sum(len(fc.keyframe_points) for fc in a.fcurves),'animated_paths':list(dict.fromkeys(fc.data_path for fc in a.fcurves))})
if meshes:
    lo,hi=bbox(meshes); report['import_bbox']={'min':vec(lo),'max':vec(hi),'dimensions':vec(hi-lo)}
with open(base+'/inspection.json','w',encoding='utf-8') as f: json.dump(report,f,ensure_ascii=False,indent=2)
print('INSPECTION_REPORT_WRITTEN', flush=True)
# Render the imported model without changing geometry, weights or materials.
for o in arms:
    o.animation_data_clear()
    o.data.pose_position='REST'
for o in meshes: o.animation_data_clear(); o.hide_render=False; o.hide_viewport=False
bpy.context.view_layer.update()
lo,hi=bbox(meshes); center=(lo+hi)*0.5; extent=max(hi-lo)
sc.render.engine='BLENDER_EEVEE'
sc.eevee.taa_render_samples=32
sc.eevee.use_gtao=True
sc.eevee.gtao_distance=extent*.12
sc.render.resolution_x=800; sc.render.resolution_y=800; sc.render.resolution_percentage=100
sc.render.image_settings.file_format='PNG'
sc.world=bpy.data.worlds.new('InspectionWorld'); sc.world.use_nodes=True
sc.world.node_tree.nodes['Background'].inputs['Color'].default_value=(.2,.2,.2,1)
sc.world.node_tree.nodes['Background'].inputs['Strength'].default_value=.6
sc.view_settings.view_transform='Standard'
sc.view_settings.look='Medium High Contrast'
for name, offset, power, size in [('Key',(2,-3,4),1300,3),('Fill',(-3,-1,2),800,3),('Rim',(1,3,3),1100,2)]:
    data=bpy.data.lights.new(name,'AREA'); data.energy=power*(extent/2)**2; data.shape='DISK'; data.size=size*extent/2
    ob=bpy.data.objects.new(name,data); sc.collection.objects.link(ob); ob.location=center+Vector(offset)*extent
    ob.rotation_euler=(center-ob.location).to_track_quat('-Z','Y').to_euler()
camdata=bpy.data.cameras.new('InspectionCamera'); cam=bpy.data.objects.new('InspectionCamera',camdata); sc.collection.objects.link(cam); sc.camera=cam
camdata.type='ORTHO'; camdata.ortho_scale=extent*1.28; camdata.clip_end=extent*100; camdata.clip_start=max(extent*.00001,0.00001)
views=[('front',(0,-1,.03)),('side',(1,0,.03)),('back',(0,1,.03)),('threequarter',(1,-1,.5))]
for name, direction in views:
    cam.location=center+Vector(direction).normalized()*extent*4
    cam.rotation_euler=(center-cam.location).to_track_quat('-Z','Y').to_euler()
    sc.render.filepath=base+'/'+name+'.png'
    bpy.ops.render.render(write_still=True)
print('INSPECTION_COMPLETE', flush=True)
