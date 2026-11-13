import sys, os, json, hashlib, pickle, collections, shutil
sys.path.insert(0,'/opt/unimate/blender-4.0.2-linux-x64/4.0/scripts/addons')
from io_scene_fbx import parse_fbx, encode_bin
base='/data/video2rig/inspections/e6f816b193f7'
out=base+'/final_export';os.makedirs(out,exist_ok=True)
name='11101_nogun_exam_textured.fbx'
original,version=parse_fbx.parse('/data/video2rig/projects/m11101_nogun/out/exam/exam.fbx')
donor,_=parse_fbx.parse(base+'/textured_export/'+name)
material_types={b'Material',b'Texture',b'Video',b'LayeredTexture'}
def child(e,id):return next(x for x in e.elems if x.id==id)
from types import SimpleNamespace

def clone(e):
    return SimpleNamespace(id=e.id,props=list(e.props),props_type=bytearray(e.props_type),elems=[clone(x) for x in e.elems])

def encoded(e):
    c=encode_bin.FBXElem(e.id)
    methods={ord('Y'):'add_int16',ord('C'):'add_char',ord('I'):'add_int32',ord('F'):'add_float32',ord('D'):'add_float64',ord('L'):'add_int64',ord('R'):'add_bytes',ord('S'):'add_string',ord('f'):'add_float32_array',ord('d'):'add_float64_array',ord('l'):'add_int64_array',ord('i'):'add_int32_array',ord('b'):'add_bool_array',ord('c'):'add_byte_array',ord('B'):'add_bool',ord('Z'):'add_int8'}
    for typ,val in zip(e.props_type,e.props):getattr(c,methods[typ])(bool(val) if typ==ord('B') else val)
    c.elems=[encoded(x) for x in e.elems]
    return c
def digest(e):
    h=hashlib.sha256();h.update(e.id);h.update(bytes(e.props_type))
    for p in e.props:
        if hasattr(p,'tobytes'):h.update(p.tobytes())
        else:h.update(pickle.dumps(p,protocol=4))
    for c in e.elems:h.update(digest(c).encode())
    return h.hexdigest()
root=clone(original);objects=child(root,b'Objects');connections=child(root,b'Connections')
dobjects=child(donor,b'Objects');dconnections=child(donor,b'Connections')
old_ids={e.props[0] for e in objects.elems if e.id in material_types}
source_nodes=[e for e in dobjects.elems if e.id in material_types]
new_ids={e.props[0]:10000000000+i for i,e in enumerate(source_nodes)}
assert set(new_ids.values()).isdisjoint({e.props[0] for e in objects.elems})
def model_name(e):return e.props[1].split(b'\x00\x01')[0]
target_models={model_name(e):e.props[0] for e in objects.elems if e.id==b'Model' and e.props[2]==b'Mesh'}
model_map={e.props[0]:target_models[model_name(e)] for e in dobjects.elems if e.id==b'Model' and e.props[2]==b'Mesh'}
# Every mesh has one material slot and identical all-zero material assignments.
for objgroup in [objects,dobjects]:
    for geo in objgroup.elems:
        if geo.id!=b'Geometry':continue
        mats=[x for x in geo.elems if x.id==b'LayerElementMaterial']
        assert len(mats)==1
        indices=child(mats[0],b'Materials').props[0]
        assert all(v==0 for v in indices)
objects.elems=[e for e in objects.elems if e.props[0] not in old_ids]
for node in source_nodes:
    new=clone(node);new.props[0]=new_ids[node.props[0]];objects.elems.append(new)
connections.elems=[e for e in connections.elems if not (e.props[1] in old_ids or e.props[2] in old_ids)]
added=[]
for c in dconnections.elems:
    a,b=c.props[1:3]
    if a not in new_ids:continue
    assert b in new_ids or b in model_map,(a,b)
    new=clone(c);new.props[1]=new_ids[a];new.props[2]=new_ids[b] if b in new_ids else model_map[b]
    connections.elems.append(new);added.append(new.props)
definitions=child(root,b'Definitions');ddefinitions=child(donor,b'Definitions')
definitions.elems=[e for e in definitions.elems if not (e.id==b'ObjectType' and e.props[0] in material_types)]
for e in ddefinitions.elems:
    if e.id==b'ObjectType' and e.props[0] in material_types:definitions.elems.append(clone(e))
counts=collections.Counter(e.id for e in objects.elems)
for e in definitions.elems:
    if e.id==b'Count':e.props[0]=sum(counts.values())+1
    if e.id==b'ObjectType':
        count=next((x for x in e.elems if x.id==b'Count'),None)
        if count is not None and e.props[0] in counts:count.props[0]=counts[e.props[0]]
path=out+'/'+name
encode_bin.write(path,encoded(root),version)
check,_=parse_fbx.parse(path)
original_objects={e.props[0]:digest(e) for e in child(original,b'Objects').elems if e.id not in material_types}
final_objects={e.props[0]:digest(e) for e in child(check,b'Objects').elems if e.id not in material_types}
if original_objects!=final_objects:
    orig={e.props[0]:e for e in child(original,b'Objects').elems}; fin={e.props[0]:e for e in child(check,b'Objects').elems}
    def differences(a,b,path):
        if digest(a)==digest(b):return
        for i,(pa,pb) in enumerate(zip(a.props,b.props)):
            same=(pa.tobytes()==pb.tobytes()) if hasattr(pa,'tobytes') and hasattr(pb,'tobytes') else pa==pb
            if not same:print('PROPERTY_DIFFERENCE',path,i,repr(pa)[:300],repr(pb)[:300],type(pa),type(pb))
        if len(a.elems)!=len(b.elems):print('CHILD_COUNT_DIFFERENCE',path,len(a.elems),len(b.elems))
        for ca,cb in zip(a.elems,b.elems):differences(ca,cb,path+'/'+ca.id.decode())
    for oid in original_objects:
        if original_objects[oid]!=final_objects[oid]:differences(orig[oid],fin[oid],str(oid))
    raise AssertionError('Nonmaterial object mismatch')
for e in original.elems:
    if e.id not in {b'Objects',b'Connections',b'Definitions'}:assert digest(e)==digest(child(check,e.id)),e.id
original_connections=[digest(e) for e in child(original,b'Connections').elems if e.props[1] not in old_ids and e.props[2] not in old_ids]
final_connections=[digest(e) for e in child(check,b'Connections').elems if e.props[1] not in new_ids.values() and e.props[2] not in new_ids.values()]
assert original_connections==final_connections
validation={'method':'lossless FBX material object replacement','non_material_objects_identical':len(original_objects),'geometry_skin_animation_nodes_unchanged':True,'non_material_connections_unchanged':True,'global_settings_and_takes_unchanged':True,'added_material_objects':dict((k.decode(),v) for k,v in collections.Counter(e.id for e in source_nodes).items()),'output_bytes':os.path.getsize(path)}
with open(out+'/lossless_validation.json','w') as f:json.dump(validation,f,indent=2)
for file in ['transfer_report.json','reference_geometry.npz']:shutil.copy2(base+'/textured_export/'+file,out+'/'+file)
print('LOSSLESS_MATERIAL_PATCH_COMPLETE',json.dumps(validation),flush=True)
