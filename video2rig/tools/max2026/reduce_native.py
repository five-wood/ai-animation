"""Reduce keys in a native-Max reference, measuring the ACTUAL Max result.
This measures reduction error separately from source->Biped retarget error.
Only the dedicated working files are loaded; original .max is never written.
"""
import os,json,math,time,traceback
import pymxs
rt=pymxs.runtime
BASE=r'D:\Project\ai-animation\work\max_editable_20261109'
SOURCE=BASE+r'\native_retarget_reference.max'
OUTPUT=BASE+r'\editable_sparse.max'
TRIAL=False

def rows(m):return [[float(m[i][j]) for j in range(3)] for i in range(4)]
def point_err(a,b):return math.sqrt(sum((a[i]-b[i])**2 for i in range(3)))
def angle_err(a,b):
    # Normalize evaluated matrix row lengths (native poses have no animated scale).
    def norm(r):
        s=math.sqrt(sum(x*x for x in r));return [x/s for x in r]
    a=[norm(r) for r in a[:3]];b=[norm(r) for r in b[:3]]
    return math.degrees(math.acos(max(-1.,min(1.,(sum(a[i][j]*b[i][j] for i in range(3) for j in range(3))-1.)/2.))))
def write(name,x):
    p=BASE+'\\'+name
    with open(p+'.tmp','w',encoding='utf8') as f:json.dump(x,f,ensure_ascii=False,indent=2)
    os.replace(p+'.tmp',p)
def tracks(names):
    found=[];seen=set()
    def scan(c,path):
        h=int(rt.getHandleByAnim(c))
        if h in seen:return
        seen.add(h)
        kind=str(rt.classOf(c));count=int(rt.numKeys(c));children=[]
        for i in range(1,int(c.numSubs)+1):
            s=rt.getSubAnim(c,i)
            try:ch=s.controller
            except:continue
            if ch is not None:children.append((ch,path+'/'+str(rt.getSubAnimName(c,i))))
        # Position/Euler compound counts aggregate the leaf tracks. Biped keys
        # are stored by the BipDriven controller even if it has non-key subanims.
        if count>=0 and ('BipDriven' in kind or not children):
            found.append((c,path,kind,h))
        for ch,p in children:scan(ch,p)
    for name in names:scan(rt.getNodeByName(name).controller,name)
    return found

def inventory(ts):
    return [{'path':p,'class':k,'handle':h,'num_keys':int(rt.numKeys(c)),
        'times_ticks':[int(rt.getKeyTime(c,i).ticks) for i in range(1,int(rt.numKeys(c))+1)]} for c,p,k,h in ts if int(rt.numKeys(c))>0]

def sample(names,ticks):
    out=[]
    for t in ticks:
        with pymxs.attime(t/160.):
            out.append({name:rows(rt.getNodeByName(name).transform) for name in names})
    return out

def main():
    global TRIAL
    current=os.path.abspath(str(rt.maxFilePath)+str(rt.maxFileName))
    if os.path.normcase(os.path.dirname(current))!=os.path.normcase(BASE):raise RuntimeError('Not a working scene')
    if rt.getSaveRequired():raise RuntimeError('Unsaved scene; refusing reset')
    if os.path.exists(OUTPUT):raise RuntimeError('Refusing to overwrite sparse scene')
    packet=json.load(open(BASE+r'\motion_packet.json',encoding='utf8'))
    names=[b['name'] for b in packet['bones'] if b['write'] or b['name'] in ['Bip001 L Calf','Bip001 L Foot','Bip001 L Toe0','Bip001 R Calf','Bip001 R Foot','Bip001 R Toe0']]
    duration=24000
    ticks=sorted(set(range(0,duration+1,80))|set(range(0,duration+1,200))) # 60Hz + source24Hz
    rt.loadMaxFile(SOURCE,quiet=True);TRIAL=True
    reference=sample(names,ticks)
    before=inventory(tracks(names))
    keyticks=sorted(set(t for tr in before for t in tr['times_ticks'] if 0<=t<=duration))
    keys=[0,duration]
    pos_tol=.15;ang_tol=1.5;history=[];started=time.time();maxkeys=80
    for iteration in range(1,50):
        rt.loadMaxFile(SOURCE,quiet=True)
        ts=tracks(names)
        keep=set(keys)
        for ctl,path,kind,h in ts:
            for i in range(int(rt.numKeys(ctl)),0,-1):
                t=int(rt.getKeyTime(ctl,i).ticks)
                if 0<t<duration and t not in keep:rt.deleteKey(ctl,i)
        actual=sample(names,ticks)
        errors=[];perbone={name:{'max_position_cm':0.,'max_angle_deg':0.} for name in names}
        for j,t in enumerate(ticks):
            peak=0.
            for name in names:
                ep=point_err(actual[j][name][3],reference[j][name][3]);ea=angle_err(actual[j][name],reference[j][name])
                d=perbone[name];d['max_position_cm']=max(ep,d['max_position_cm']);d['max_angle_deg']=max(ea,d['max_angle_deg'])
                peak=max(peak,ep/pos_tol,ea/ang_tol)
            errors.append(peak)
        worst=max(range(len(ticks)),key=lambda j:errors[j]);peak=errors[worst]
        inv=inventory(ts)
        rec={'iteration':iteration,'pose_time_count':len(keys),'kept_ticks':keys.copy(),'max_normalized_error':peak,'worst_ticks':ticks[worst],
             'actual_stored_keys':sum(x['num_keys'] for x in inv),'per_bone':perbone}
        history.append(rec)
        write('reduction_progress.json',{'status':'working','history':history,'elapsed_seconds':time.time()-started})
        if peak<=1. or len(keys)>=maxkeys:
            rt.sliderTime=0
            if not rt.saveMaxFile(OUTPUT,useNewFile=True,clearNeedSaveFlag=True,quiet=True):raise RuntimeError('Saving sparse scene failed')
            write('reduction_report.json',{'status':'passed_native_reference' if peak<=1. else 'budget_exceeded_diagnostic',
              'output':OUTPUT,'native_reference':SOURCE,'source_retarget_status':'not_accepted_upper_limb_mismatch',
              'evaluated_times_count':len(ticks),'evaluation':'60Hz plus exact 24Hz source times; Max actual transforms',
              'position_tolerance_cm':pos_tol,'angle_tolerance_deg':ang_tol,'kept_pose_times':len(keys),
              'dense_unique_controller_key_count':sum(x['num_keys'] for x in before),
              'sparse_unique_controller_key_count':sum(x['num_keys'] for x in inv),
              'key_count_note':'Unique keyed leaf/BipDriven controllers, not semantic limb poses; Biped shared-storage aliases may remain.',
              'before_tracks':before,'after_tracks':inv,'history':history,'contact_status':'not_locked',
              'elapsed_seconds':time.time()-started})
            write('sparse_actual_60hz.json',{'ticks':ticks,'samples':actual})
            print('V2R sparse native scene:',OUTPUT,'poses',len(keys),'peak',peak)
            return
        # For the worst segment choose the nearest available original key time.
        ranked=sorted(range(len(ticks)),key=lambda j:errors[j],reverse=True)
        add=[]
        for j in ranked:
            candidates=[k for k in keyticks if k not in keep and k not in add]
            if not candidates:break
            k=min(candidates,key=lambda k:abs(k-ticks[j]))
            if all(abs(k-q)>=400 for q in add):add.append(k)
            if len(add)>=4:break
        if not add:raise RuntimeError('No remaining keys')
        keys=sorted(set(keys+add))
    raise RuntimeError('Reduction iteration limit')
try:
    main()
except Exception:
    error=traceback.format_exc()
    if TRIAL:
        try:rt.loadMaxFile(SOURCE,quiet=True)
        except:error+='\nRollback failed:\n'+traceback.format_exc()
    write('reduction.error.json',{'error':error})
    raise
