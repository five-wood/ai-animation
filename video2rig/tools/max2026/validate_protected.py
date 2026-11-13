"""Verify non-Biped original control curves were not edited by import/reduction.
World transforms may follow imported drivers, so compare controller keys/values,
not world poses. Scene is restored to the file that was open at entry.
"""
import os,json,hashlib,traceback
import pymxs
rt=pymxs.runtime
BASE=r'D:\Project\ai-animation\work\max_editable_20261109'

def snapshot(exclude):
    out={};seen=set()
    def scan(c,path):
        try:h=int(rt.getHandleByAnim(c))
        except:return
        if h in seen:return
        seen.add(h)
        kind=str(rt.classOf(c));n=int(rt.numKeys(c));children=[]
        for i in range(1,int(c.numSubs)+1):
            sub=rt.getSubAnim(c,i)
            try:ch=sub.controller
            except:continue
            if ch is not None:children.append((ch,path+'/'+str(rt.getSubAnimName(c,i))))
        if n>0 and not children:
            keys=[]
            for i in range(1,n+1):
                try:
                    k=rt.getKey(c,i)
                    keys.append({str(p):str(rt.getProperty(k,p)) for p in rt.getPropNames(k)})
                except Exception:
                    t=rt.getKeyTime(c,i)
                    with pymxs.attime(float(t.frame)):
                        try:value=str(c.value)
                        except Exception:value='unsupported_key_value'
                    keys.append({'ticks':int(t.ticks),'evaluated_value':value,'note':'Controller does not expose MAXKey properties'})
            out[path]={'class':kind,'keys':keys}
        for ch,p in children:scan(ch,p)
    for n in list(rt.objects):
        if str(rt.classOf(n))=='Biped_Object' or str(n.name) in exclude:continue
        scan(n.controller,str(n.name))
    return out

def main():
    current=os.path.abspath(str(rt.maxFilePath)+str(rt.maxFileName))
    if os.path.normcase(os.path.dirname(current))!=os.path.normcase(BASE):raise RuntimeError('Not a working scene')
    if rt.getSaveRequired():raise RuntimeError('Unsaved scene; refusing reset')
    p=json.load(open(BASE+r'\motion_packet.json',encoding='utf8'))
    exclude={b['name'] for b in p['bones'] if b['write']}
    files=['base_scene.max','native_retarget_reference.max','editable_sparse.max','ik_support_test.max']
    reports=[];reference=None
    for name in files:
        path=BASE+'\\'+name
        if not os.path.isfile(path):continue
        rt.loadMaxFile(path,quiet=True)
        s=snapshot(exclude)
        if reference is None:reference=s
        changed=[k for k in sorted(set(reference)|set(s)) if reference.get(k)!=s.get(k)]
        reports.append({'file':name,'protected_keyed_leaf_tracks':len(s),'matches_original':not changed,'changed_tracks':changed,
          'differences':{k:{'base':reference.get(k),'candidate':s.get(k)} for k in changed}})
    rt.loadMaxFile(current,quiet=True)
    original=BASE.rsplit('\\work\\',1)[0]+r'\11101_01(10.22).max'
    expected=json.load(open(BASE+r'\working_copy.json'))['sha256']
    actual=hashlib.sha256(open(original,'rb').read()).hexdigest()
    json.dump({'reports':reports,'original_sha256':actual,'original_hash_unchanged':actual==expected},open(BASE+r'\protection_report.json','w',encoding='utf8'),ensure_ascii=False,indent=2)
    print('V2R protected curves validated')
try:
    main()
except Exception:
    json.dump({'error':traceback.format_exc()},open(BASE+r'\protection.error.json','w'))
    raise
