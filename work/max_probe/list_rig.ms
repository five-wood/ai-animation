-- List rig node classes / controllers of the opened scene (read-only probe).
out = createFile @"D:\Project\ai-animation\work\max_probe\rig_list.txt"
format "file: % \n" maxFilePath maxFileName to:out
format "frameRate: %  animationRange: %\n" frameRate animationRange to:out
bipRoots = #()
counts = #()
for o in objects do (
    c = (classOf o) as string
    found = false
    for e in counts where e[1] == c do (e[2] += 1; found = true)
    if not found do append counts #(c, 1)
    if classOf o == Biped_Object and o.controller != undefined and classOf o.controller == Vertical_Horizontal_Turn do append bipRoots o
)
format "\n-- node classes --\n" to:out
for e in counts do format "%: %\n" e[1] e[2] to:out
format "\n-- biped roots (COM) --\n" to:out
for b in bipRoots do (
    bc = b.controller
    format "% figureMode:% rootName:% height:%\n" b.name bc.figureMode bc.rootName bc.height to:out
)
format "\n-- skin modifiers --\n" to:out
for o in objects where o.modifiers.count > 0 do (
    for m in o.modifiers do format "% -> % \n" o.name (classOf m) to:out
)
format "\n-- hierarchy (name | class | controller | parent | keys) --\n" to:out
for o in objects where (classOf o != Editable_Mesh and classOf o != Editable_Poly and classOf o != PolyMeshObject) do (
    p = if o.parent == undefined then "-" else o.parent.name
    ctl = if o.controller == undefined then "-" else (classOf o.controller) as string
    nk = 0
    try (nk = o.controller.keys.count) catch ()
    format "% | % | % | % | %\n" o.name (classOf o) ctl p nk to:out
)
close out
