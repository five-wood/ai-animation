"""Viewport-only previews; restore sparse output and open tested launcher."""
import os,json,traceback
import pymxs
rt=pymxs.runtime
BASE=r'D:\Project\ai-animation\work\max_editable_20261109'
try:
    current=os.path.abspath(str(rt.maxFilePath)+str(rt.maxFileName))
    if os.path.normcase(os.path.dirname(current))!=os.path.normcase(BASE):raise RuntimeError('Not working scene')
    if rt.getSaveRequired():raise RuntimeError('Unsaved scene')
    for filename,frame,out in [('editable_sparse.max',75,'sparse_preview.png'),('ik_support_test.max',30,'ik_preview.png')]:
        rt.loadMaxFile(BASE+'\\'+filename,quiet=True)
        rt.sliderTime=frame
        rt.redrawViews()
        bmp=rt.gw.getViewportDib()
        bmp.filename=BASE+'\\'+out
        rt.save(bmp);rt.close(bmp)
    rt.loadMaxFile(BASE+r'\editable_sparse.max',quiet=True)
    rt.sliderTime=0
    rt.redrawViews()
    rt.fileIn(r'D:\Project\ai-animation\video2rig\tools\max2026\V2R_Max2026.ms')
    json.dump({'preview_files':['sparse_preview.png','ik_preview.png'],'current_scene':str(rt.maxFilePath)+str(rt.maxFileName),'current_frame':str(rt.sliderTime),'launcher_loaded':True},open(BASE+r'\preview_status.json','w'),indent=2)
except Exception:
    json.dump({'error':traceback.format_exc()},open(BASE+r'\preview.error.json','w'))
    raise
