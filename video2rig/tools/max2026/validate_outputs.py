"""Compare sparse candidates against dense native reference at 60Hz+source times."""
import os,json,math,traceback
import pymxs
rt=pymxs.runtime
BASE=r'D:\Project\ai-animation\work\max_editable_20261109'

def rows(m):return [[float(m[i][j]) for j in range(3)] for i in range(4)]
def poserr(a,b):return math.sqrt(sum((a[i]-b[i])**2 for i in range(3)))
def angerr(a,b):
    def unit(v):
        d=math.sqrt(sum(x*x for x in v));return [x/d for x in v]
    A=[unit(x) for x in a[:3]];B=[unit(x) for x in b[:3]]
    return math.degrees(math.acos(max(-1,min(1,(sum(A[i][j]*B[i][j] for i in range(3) for j in range(3))-1)/2))))
def inventory(names):
    out=[];seen=set()
    def scan(c,path):
        h=int(rt.getHandleByAnim(c))
        if h in seen:return
        seen.add(h);kind=str(rt.classOf(c));n=int(rt.numKeys(c));children=[]
        for i in range(1,int(c.numSubs)+1):
            s=rt.getSubAnim(c,i)
            try:ch=s.controller
            except:continue
            if ch is not None:children.append((ch,path+'/'+str(rt.getSubAnimName(c,i))))
        if n>0 and ('BipDriven' in kind or not children):
            out.append({'path':path,'class':kind,'num_keys':n,'ticks':[int(rt.getKeyTime(c,i).ticks) for i in range(1,n+1)]})
        for ch,p in children:scan(ch,p)
    for name in names:scan(rt.getNodeByName(name).controller,name)
    return out

def main():
    current=os.path.abspath(str(rt.maxFilePath)+str(rt.maxFileName))
    if os.path.normcase(os.path.dirname(current))!=os.path.normcase(BASE):raise RuntimeError('Not working scene')
    if rt.getSaveRequired():raise RuntimeError('Unsaved scene')
    p=json.load(open(BASE+r'\motion_packet.json',encoding='utf8'))
    names=[b['name'] for b in p['bones'] if b['write'] or b['name'] in ['Bip001 L Calf','Bip001 L Foot','Bip001 L Toe0','Bip001 R Calf','Bip001 R Foot','Bip001 R Toe0']]
    ticks=sorted(set(range(0,24001,80))|set(range(0,24001,200)))
    files=['native_retarget_reference.max','editable_sparse.max','native_mocap_reduced.max']
    reports=[];ref=None
    for filename in files:
        rt.loadMaxFile(BASE+'\\'+filename,quiet=True)
        samples=[]
        for t in ticks:
            with pymxs.attime(t/160.):samples.append({name:rows(rt.getNodeByName(name).transform) for name in names})
        if ref is None:ref=samples
        perbone={name:{'max_position_cm':0.,'max_angle_deg':0.} for name in names}
        for a,b in zip(samples,ref):
            for name in names:
                m=perbone[name];m['max_position_cm']=max(m['max_position_cm'],poserr(a[name][3],b[name][3]));m['max_angle_deg']=max(m['max_angle_deg'],angerr(a[name],b[name]))
        inv=inventory(names)
        accepted=all(x['max_position_cm']<=.15 and x['max_angle_deg']<=1.5 for x in perbone.values())
        reports.append({'file':filename,'passed_native_reference':accepted,'max_position_cm':max(x['max_position_cm'] for x in perbone.values()),'max_angle_deg':max(x['max_angle_deg'] for x in perbone.values()),'controller_key_count':sum(x['num_keys'] for x in inv),'unique_key_times':len(set(t for x in inv for t in x['ticks'])),'per_bone':perbone,'inventory':inv})
    rt.loadMaxFile(current,quiet=True)
    json.dump({'evaluation_times':len(ticks),'position_tolerance_cm':.15,'angle_tolerance_deg':1.5,'reference':'native_retarget_reference.max','source_fidelity_warning':'Native elbow/attachment differences remain; this only measures reduction error.','reports':reports},open(BASE+r'\candidate_comparison.json','w',encoding='utf8'),ensure_ascii=False,indent=2)
    print('V2R candidate comparison complete')
try:
    main()
except Exception:
    json.dump({'error':traceback.format_exc()},open(BASE+r'\candidate_comparison.error.json','w'))
    raise
