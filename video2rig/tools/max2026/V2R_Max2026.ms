-- V2R / 3ds Max 2026 local validation prototype.
-- Run through Scripting > Run Script. Original rig is never silently replaced.
-- This launcher is for the inspected m11101 scene, NOT arbitrary-rig production import.
(
    global V2RMax2026Dialog
    try(destroyDialog V2RMax2026Dialog)catch()
    rollout V2RMax2026Dialog "V2R - Max 2026 validation" width:390 height:530
    (
        label info1 "Local prototype: native Biped + Bone" pos:[15,12] width:360
        label info2 "2026-11-12: keyframe-first version (artist-approved keys)" pos:[15,34] width:370
        button kf "Open ladingwu KEYFRAME edit (34 whole-body keys)" pos:[15,62] width:360 height:28
        button kf50 "Open ladingwu keyframe edit + 16 breakdowns (50 keys)" pos:[15,96] width:360 height:28
        button kfFolder "Open keyframe folder (acceptance notes, overlay video)" pos:[15,130] width:360 height:28
        label old1 "Older versions (for comparison):" pos:[15,172] width:360
        button lad "Open 2026-11-11 ladingwu spline edit (177 key frames)" pos:[15,192] width:360 height:28
        button ladFolder "Open 2026-11-11 ladingwu folder" pos:[15,226] width:360 height:28
        button spline "Open 2026-11-11 m11101 spline edit (5s, in place)" pos:[15,260] width:360 height:28
        button inspect "Read-only scene inspection" pos:[15,304] width:360 height:28
        button sparse "Open 2026-11-09 sparse copy (old, full-body times)" pos:[15,338] width:360 height:28
        button ik "Open 2026-11-09 synthetic IK support test" pos:[15,372] width:360 height:28
        button report "Open 2026-11-09 validation report folder" pos:[15,406] width:360 height:28
        label warning "Do not overwrite the original character scene." pos:[15,452] width:360
        label warning2 "Save your own edits under a new file name." pos:[15,472] width:360
        fn openCopy file =
        (
            if not doesFileExist file then
                messageBox ("Output not found:\n" + file) title:"V2R"
            else if getSaveRequired() then
                messageBox "Current scene has unsaved changes. Save your work before opening a test copy." title:"V2R"
            else if queryBox "Open the validation copy? This replaces the scene in memory, not the original file on disk." title:"V2R" then
                loadMaxFile file quiet:true
        )
        on kf pressed do
            openCopy @"D:\Project\ai-animation\work\max_keyframe_20261112_ladingwu\v4\keyframe_edit.max"
        on kf50 pressed do
            openCopy @"D:\Project\ai-animation\work\max_keyframe_20261112_ladingwu\v4_bd16\keyframe_edit_bd16.max"
        on kfFolder pressed do
            shellLaunch @"D:\Project\ai-animation\work\max_keyframe_20261112_ladingwu" ""
        on lad pressed do
            openCopy @"D:\Project\ai-animation\work\max_editable_20261111_ladingwu\spline_edit.max"
        on ladFolder pressed do
            shellLaunch @"D:\Project\ai-animation\work\max_editable_20261111_ladingwu" ""
        on spline pressed do
            openCopy @"D:\Project\ai-animation\work\max_editable_20261111_spline\spline_edit.max"
        on inspect pressed do
            python.ExecuteFile @"D:\Project\ai-animation\video2rig\tools\max2026\inspect_scene.py"
        on sparse pressed do
            openCopy @"D:\Project\ai-animation\work\max_editable_20261109\editable_sparse.max"
        on ik pressed do
            openCopy @"D:\Project\ai-animation\work\max_editable_20261109\ik_support_test.max"
        on report pressed do
            shellLaunch @"D:\Project\ai-animation\work\max_editable_20261109" ""
    )
    createDialog V2RMax2026Dialog
)
