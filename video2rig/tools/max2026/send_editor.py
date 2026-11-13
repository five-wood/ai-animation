"""Windows-only existing-Max editor dispatch. No new application/process.
Only operates on an Untitled Max scripting editor (or our own previous command).
The Win32 editor/menu technique is locally tested, not a public Autodesk API.
"""
import argparse,ctypes,json
from ctypes import wintypes as w
p=argparse.ArgumentParser()
p.add_argument('--pid',type=int,required=True)
p.add_argument('script')
a=p.parse_args()
u=ctypes.windll.user32
u.SendMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];u.SendMessageW.restype=ctypes.c_ssize_t
u.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]
CB=ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
editors=[]
def top(h,l):
 pid=w.DWORD();u.GetWindowThreadProcessId(h,ctypes.byref(pid))
 c=ctypes.create_unicode_buffer(256);u.GetClassNameW(h,c,256)
 b=ctypes.create_unicode_buffer(1024);u.GetWindowTextW(h,b,1024)
 if pid.value==a.pid and c.value=='MXS_SciTEWindow':editors.append((h,b.value))
 return True
cb=CB(top);u.EnumWindows(cb,0)
if len(editors)!=1:raise SystemExit('Expected one existing editor: '+repr(editors))
editor,title=editors[0]
if 'Untitled' not in title and '无标题' not in title:raise SystemExit('Refusing to replace a named editor buffer: '+title)
controls=[]
def child(h,l):
 c=ctypes.create_unicode_buffer(256);u.GetClassNameW(h,c,256)
 if c.value=='MXS_Scintilla':controls.append(h)
 return True
cc=CB(child);u.EnumChildWindows(editor,cc,0)
if not controls:raise SystemExit('No editor text control')
h=controls[0]
b=ctypes.create_unicode_buffer(16384);u.SendMessageW(h,13,16384,ctypes.addressof(b))
if b.value.strip() and not b.value.strip().startswith('python.ExecuteFile @"D:\\Project\\ai-animation\\video2rig\\tools\\max2026\\'):
 raise SystemExit('Refusing to overwrite unrelated scratch text')
from pathlib import Path
script=Path(a.script).resolve()
if not script.is_file():raise SystemExit('Missing script')
cmd='python.ExecuteFile @"'+str(script)+'"\r\n'
s=ctypes.create_unicode_buffer(cmd);u.SendMessageW(h,12,0,ctypes.addressof(s))
u.SendMessageW(editor,0x111,1401,0)  # Language / MAXScript
u.PostMessageW(editor,0x111,301,0)   # Tools / Evaluate All, exactly once
print(json.dumps({'pid':a.pid,'editor':int(editor),'script':str(script)}))
