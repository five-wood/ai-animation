import bpy, math, os
from mathutils import Matrix, Vector
base='/data/video2rig/inspections/e6f816b193f7'
# Reuse inspection setup and data extraction, but skip its render loop.
source=open(base+'/inspect.py',encoding='utf-8').read().split('for name, direction in views:')[0]
exec(compile(source,base+'/inspect.py','exec'))
roots=[o for o in bpy.context.scene.objects if o.type in {'MESH','EMPTY'} and o.parent is None]
R=Matrix.Rotation(math.radians(-90),4,'X')
for o in roots: o.matrix_world=R@o.matrix_world
bpy.context.view_layer.update()
lo,hi=bbox(meshes); center=(lo+hi)*.5; extent=max(hi-lo)
camdata.ortho_scale=extent*1.25
for name,direction in [('upright_front',(0,-1,.04)),('upright_threequarter',(1,-2,.6)),('upright_back',(0,1,.04)),('upright_side',(1,0,.04))]:
    cam.location=center+Vector(direction).normalized()*extent*4
    cam.rotation_euler=(center-cam.location).to_track_quat('-Z','Y').to_euler()
    sc.render.filepath=base+'/'+name+'.png'
    bpy.ops.render.render(write_still=True)
