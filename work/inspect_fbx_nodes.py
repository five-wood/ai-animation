import sys, inspect, json
sys.path.insert(0,'/opt/unimate/blender-4.0.2-linux-x64/4.0/scripts/addons')
from io_scene_fbx import parse_fbx, encode_bin
print('WRITE_API',inspect.signature(encode_bin.write)); print('ELEM_API',inspect.signature(encode_bin.FBXElem));print('METHODS',[x for x in dir(encode_bin.FBXElem) if not x.startswith('_')])
paths=['/data/video2rig/projects/m11101_nogun/out/exam/exam.fbx','/data/video2rig/inspections/e6f816b193f7/textured_export/11101_nogun_exam_textured.fbx']
for p in paths:
    root,version=parse_fbx.parse(p)
    print('FILE',p,'VERSION',version,'ROOT_TYPE',type(root),'PROPS_TYPE',type(root.props_type))
    print('TOP',[(e.id,len(e.elems)) for e in root.elems])
    objs=next(e for e in root.elems if e.id==b'Objects')
    print('OBJECTS',[(e.id,e.props[:3]) for e in objs.elems if e.id in {b'Model',b'Material',b'Texture',b'Video'}])
    ids={e.props[0] for e in objs.elems if e.id in {b'Material',b'Texture',b'Video'}}
    con=next(e for e in root.elems if e.id==b'Connections')
    print('CONNECTIONS', [e.props for e in con.elems if len(e.props)>2 and (e.props[1] in ids or e.props[2] in ids)])
    for e in objs.elems:
        if e.id==b'Geometry':print('GEO',e.props,[(x.id,x.props) for x in e.elems if x.id==b'LayerElementMaterial'])
        if e.id==b'Video': print('VIDEO',e.props,[(x.id,[len(v) if isinstance(v,bytes) else v for v in x.props]) for x in e.elems])
