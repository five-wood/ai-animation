import bpy,numpy as np,json
from mathutils import Matrix
B='/data/video2rig/inspections/e6f816b193f7';D=B+'/diagnostic_roundtrip'
def a(m):return np.array(m,dtype=float)
def cap(path):
 bpy.ops.wm.read_factory_settings(use_empty=True);bpy.ops.import_scene.fbx(filepath=path,use_anim=True,anim_offset=0)
 arm=next(o for o in bpy.context.scene.objects if o.type=='ARMATURE');out={}
 for f in [1,15,30]:
  bpy.context.scene.frame_set(f);out[f]={}
  for b in arm.pose.bones:
   lm=b.parent.matrix.inverted_safe()@b.matrix if b.parent else b.matrix
   loc,rot,scale=lm.decompose();rt=Matrix.LocRotScale(loc,rot,scale)
   out[f][b.name]={'local':a(lm),'pose':a(b.matrix),'residual':float(np.max(np.abs(a(lm)-a(rt)))),'scale':list(scale),'basis_scale':list(b.scale),'inherit':b.bone.inherit_scale,'parent':b.parent.name if b.parent else None,'basis':a(b.matrix_basis),'decomp':a(rt)}
 return out
r=cap('/data/video2rig/projects/m11101_nogun/out/exam/exam.fbx');o=cap(D+'/baseline.fbx');summary={}
for f,bs in r.items():
 summary[f]={'worst_shear':sorted([(n,b['residual'],b['scale'],b['inherit']) for n,b in bs.items()],key=lambda x:-x[1])[:20],'worst_local_error':sorted([(n,float(np.max(np.abs(b['local']-o[f][n]['local'])))) for n,b in bs.items()],key=lambda x:-x[1])[:20],'worst_nonscale':sorted([(n,max(b['basis_scale'])-min(b['basis_scale']),b['basis_scale']) for n,b in bs.items()],key=lambda x:-x[1])[:20], 'specific':{n:{k:(v.tolist() if isinstance(v,np.ndarray) else v) for k,v in b.items()} for n,b in bs.items() if n in ['Bip001','Bip001 Pelvis','Bip001 Spine','Bip001 Spine1','Bip001 Head','Bip001 L Clavicle','Bip001 L UpperArm','Bip001 R Clavicle','Bip001 R UpperArm']}}
with open(D+'/shear_report.json','w') as h:json.dump(summary,h,indent=2)
print('SHEAR_DONE',json.dumps({f:s['worst_shear'][:3] for f,s in summary.items()}))
