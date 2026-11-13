"""Read-only Max session probe (run inside Max 2026). Writes one JSON file; changes nothing."""
import json, os, time
import pymxs
rt = pymxs.runtime
OUT = r'D:\Project\ai-animation\work\max_editable_20261111_spline\session_probe.json'
info = {'time': time.strftime('%Y-%m-%d %H:%M:%S')}
try:
    info['file'] = os.path.join(str(rt.maxFilePath), str(rt.maxFileName))
    info['save_required'] = bool(rt.getSaveRequired())
    info['max_version'] = [str(x) for x in rt.maxVersion()]
    info['batch_mode'] = bool(rt.GetQuietMode())
    info['frame_rate'] = int(rt.frameRate)
    info['animation_range'] = [float(rt.animationRange.start.frame), float(rt.animationRange.end.frame)]
    info['slider_time'] = float(rt.sliderTime.frame)
    com = rt.getNodeByName('Bip001')
    if com:
        c = com.controller
        info['biped'] = {'figure': bool(c.figureMode), 'footstep': bool(c.footstepMode), 'mixer': bool(c.mixerMode),
                         'layers': int(rt.biped.numLayers(c)), 'separate_fk_tracks': None}
    info['undo_enabled'] = True
except Exception as e:  # report, never raise inside the host session
    info['error'] = repr(e)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT + '.tmp', 'w', encoding='utf8') as f:
    json.dump(info, f, ensure_ascii=False, indent=2)
os.replace(OUT + '.tmp', OUT)
print('V2R session probe', info)
