import bpy, json, os, numpy as np
from mathutils import Vector
base='/data/video2rig/inspections/e6f816b193f7'
target='/data/video2rig/projects/m11101_nogun/out/exam/exam.fbx'
def extract(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=path,use_anim=True)
    out=[]
    for o in bpy.context.scene.objects:
        if o.type=='ARMATURE':
            print('ARMATURE',o.name,len(o.data.bones),'ACTION',o.animation_data.action.name if o.animation_data and o.animation_data.action else None)
        if o.type!='MESH': continue
        coords=np.array([v.co[:] for v in o.data.vertices]); world=np.array([(o.matrix_world@v.co)[:] for v in o.data.vertices])
        item={'name':o.name,'vertices':len(coords),'polygons':len(o.data.polygons),'loops':len(o.data.loops),'materials':[m.name if m else None for m in o.data.materials], 'uvs':[u.name for u in o.data.uv_layers], 'scale':list(o.scale),'rotation':list(o.rotation_euler),'local_min':coords.min(0).tolist(),'local_max':coords.max(0).tolist(),'world_min':world.min(0).tolist(),'world_max':world.max(0).tolist(),'vertex_groups':len(o.vertex_groups),'modifiers':[m.type for m in o.modifiers]}
        out.append((item,coords, np.array([l.vertex_index for l in o.data.loops]),[np.array([l.uv[:] for l in uv.data]) for uv in o.data.uv_layers]))
    return out
src=extract(base+'/model.fbx'); dst=extract(target)
r={'source':[x[0] for x in src],'target':[x[0] for x in dst],'comparisons':[]}
for t,tc,tl,tu in dst:
    for s,sc,sl,su in src:
        if tc.shape==sc.shape:
            tn=(tc-tc.mean(0))/max(np.ptp(tc,axis=0).max(),1e-12); sn=(sc-sc.mean(0))/max(np.ptp(sc,axis=0).max(),1e-12)
            x={'target':t['name'],'source':s['name'],'loop_indices_equal':bool(np.array_equal(tl,sl)),'normalized_coords_rmse':float(np.sqrt(np.mean((tn-sn)**2))),'uv_compare':[]}
            for i,uvt in enumerate(tu):
                for j,uvs in enumerate(su):
                    if uvt.shape==uvs.shape: x['uv_compare'].append({'target_layer':i,'source_layer':j,'max_diff':float(np.abs(uvt-uvs).max())})
            r['comparisons'].append(x)
with open(base+'/comparison.json','w') as f:json.dump(r,f,indent=2)
print(json.dumps(r,indent=2))
