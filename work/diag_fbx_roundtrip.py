import bpy, numpy as np, json, os
B='/data/video2rig/inspections/e6f816b193f7'
D=B+'/diagnostic_roundtrip';os.makedirs(D,exist_ok=True)
T='/data/video2rig/projects/m11101_nogun/out/exam/exam.fbx'
F=[1,15,30,45,60,75,90,120,150]
def mat(m):return np.array(m,dtype=float)
def capture(path):
 bpy.ops.wm.read_factory_settings(use_empty=True)
 bpy.ops.import_scene.fbx(filepath=path,use_anim=True,anim_offset=0)
 sc=bpy.context.scene; arm=next(o for o in sc.objects if o.type=='ARMATURE')
 out={'fps':[sc.render.fps,sc.render.fps_base], 'range':list(arm.animation_data.action.frame_range),'objects':{},'rest':{},'pose':{},'geometry':{},'raw':{},'modifiers':{}}
 for o in sc.objects:
  out['objects'][o.name]={'matrix':mat(o.matrix_world),'parent':o.parent.name if o.parent else None,'scale':list(o.scale),'parent_inverse':mat(o.matrix_parent_inverse)}
  if o.type=='MESH':
   out['raw'][o.name]=np.array([v.co[:] for v in o.data.vertices]);out['modifiers'][o.name]=[(m.type,getattr(m,'use_deform_preserve_volume',None),getattr(m,'use_vertex_groups',None)) for m in o.modifiers]
 out['rest']={b.name:mat(b.matrix_local) for b in arm.data.bones}
 out['keytimes']={fc.data_path+'['+str(fc.array_index)+']':[[float(k.co.x),float(k.co.y)] for k in fc.keyframe_points][:5] for fc in arm.animation_data.action.fcurves}
 for f in F:
  sc.frame_set(f);dg=bpy.context.evaluated_depsgraph_get();out['geometry'][f]={};out['pose'][f]={b.name:mat(b.matrix) for b in arm.pose.bones}
  for o in sc.objects:
   if o.type!='MESH':continue
   ev=o.evaluated_get(dg);me=ev.to_mesh();out['geometry'][f][o.name]=np.array([(ev.matrix_world@v.co)[:] for v in me.vertices]);ev.to_mesh_clear()
 return out
ref=capture(T)
sc=bpy.context.scene;sc.frame_start=1;sc.frame_end=150;sc.frame_set(1)
bpy.ops.export_scene.fbx(filepath=D+'/baseline.fbx',object_types={'ARMATURE','MESH','EMPTY'},bake_anim=True,bake_anim_use_all_actions=False,bake_anim_use_nla_strips=False,bake_anim_force_startend_keying=True,bake_anim_simplify_factor=0,add_leaf_bones=False)
def diff(a,b):return float(np.max(np.abs(a-b)))
def compare(out):
 return {'fps':out['fps'],'range':out['range'],'geometry':{f:{n:diff(v,out['geometry'][f][n]) for n,v in ns.items()} for f,ns in ref['geometry'].items()},'raw':{n:diff(v,out['raw'][n]) for n,v in ref['raw'].items()},'rest':sorted([(n,diff(v,out['rest'][n])) for n,v in ref['rest'].items()],key=lambda x:-x[1]),'pose':{f:sorted([(n,diff(v,out['pose'][f][n])) for n,v in ns.items()],key=lambda x:-x[1])[:10] for f,ns in ref['pose'].items()},'objects':{n:{'matrix_error':diff(v['matrix'],out['objects'][n]['matrix']),'scale':out['objects'][n]['scale'],'parent_inverse_error':diff(v['parent_inverse'],out['objects'][n]['parent_inverse'])} for n,v in ref['objects'].items()},'modifiers':out['modifiers']}
bas=capture(D+'/baseline.fbx');prod=capture(B+'/textured_export/11101_nogun_exam_textured.fbx')
report={'baseline':compare(bas),'production':compare(prod),'ref_fps':ref['fps'],'ref_modifiers':ref['modifiers'],'ref_objects':{n:{k:v.tolist() if isinstance(v,np.ndarray) else v for k,v in x.items()} for n,x in ref['objects'].items()},'ref_keys':ref['keytimes'],'output_keys':prod['keytimes']}
with open(D+'/roundtrip_report.json','w') as h:json.dump(report,h,indent=2)
print('DIAG_DONE',json.dumps({k:max(v for ns in report[k]['geometry'].values() for v in ns.values()) for k in ['baseline','production']}))
