import bpy
p='/data/video2rig/'
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=p+'projects/m11101_nogun/out/exam/exam.fbx',use_anim=True,anim_offset=0)
sc=bpy.context.scene
print('TARGET_TIMING',sc.render.fps,sc.render.fps_base,sc.frame_start,sc.frame_end)
arm=next(o for o in sc.objects if o.type=='ARMATURE');a=arm.animation_data.action
print('TARGET_ACTION',a.frame_range[:],len(a.fcurves))
print('FIRST_CURVE',a.fcurves[0].data_path,[(x.co[:]) for x in a.fcurves[0].keyframe_points][:5])
bpy.ops.import_scene.fbx(filepath=p+'inspections/e6f816b193f7/model.fbx',use_anim=False)
print('AFTER_SOURCE_TIMING',sc.render.fps,sc.render.fps_base,sc.frame_start,sc.frame_end)
print('AFTER_SOURCE_ACTION',a.frame_range[:],len(a.fcurves))
