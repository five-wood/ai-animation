"""Native Biped mocap key-reduction baseline in a separate working copy.
Restores global mocap settings. Keeps footstep extraction disabled.
"""
import json,os,traceback
import pymxs
rt=pymxs.runtime
BASE=r'D:\Project\ai-animation\work\max_editable_20261109'
SOURCE=BASE+r'\native_retarget_reference.max'
OUT=BASE+r'\native_mocap_reduced.max'
BIP=BASE+r'\native_reference.bip'
try:
    current=os.path.abspath(str(rt.maxFilePath)+str(rt.maxFileName))
    if os.path.normcase(os.path.dirname(current))!=os.path.normcase(BASE):raise RuntimeError('Not working scene')
    if rt.getSaveRequired():raise RuntimeError('Unsaved working scene')
    if os.path.exists(OUT) or os.path.exists(BIP):raise RuntimeError('Output already exists')
    rt.loadMaxFile(SOURCE,quiet=True)
    ctl=rt.getNodeByName('Bip001').controller
    rt.biped.saveBipFile(ctl,BIP)
    attrs=['fsExtractionMode','fsConversionMode','allTol','allSpacing','allFilter','horzTol','horzSpacing','horzFilter','rotTol','rotSpacing','rotFilter','vertTol','vertSpacing','vertFilter','pelvisTol','pelvisSpacing','pelvisFilter','spineTol','spineSpacing','spineFilter','neckTol','neckSpacing','neckFilter','leftArmTol','leftArmSpacing','leftArmFilter','rightArmTol','rightArmSpacing','rightArmFilter','leftLegTol','leftLegSpacing','leftLegFilter','rightLegTol','rightLegSpacing','rightLegFilter']
    saved={name:rt.getProperty(rt.mocap,rt.Name(name)) for name in attrs}
    try:
        rt.mocap.fsExtractionMode=0;rt.mocap.fsConversionMode=1
        for name in attrs:
            if name.endswith('Tol'):rt.setProperty(rt.mocap,rt.Name(name),.15 if name in ['horzTol','vertTol'] else 1.5)
            if name.endswith('Spacing'):rt.setProperty(rt.mocap,rt.Name(name),1)
            if name.endswith('Filter'):rt.setProperty(rt.mocap,rt.Name(name),True)
        rt.biped.loadMocapFile(ctl,BIP,rt.Name('noRedraw'))
        rt.sliderTime=0
        if not rt.saveMaxFile(OUT,useNewFile=True,clearNeedSaveFlag=True,quiet=True):raise RuntimeError('Save failed')
        json.dump({'status':'native_baseline_created_requires_validation','output':OUT,'reference':SOURCE,'footstep_extraction':False,'tolerance_position_cm':.15,'tolerance_rotation_deg':1.5,'minimum_key_spacing':1,'ordinary_bones_not_reduced':True},open(BASE+r'\native_mocap_report.json','w'),indent=2)
    finally:
        for name,value in saved.items():rt.setProperty(rt.mocap,rt.Name(name),value)
except Exception:
    json.dump({'error':traceback.format_exc()},open(BASE+r'\native_mocap.error.json','w'))
    raise
