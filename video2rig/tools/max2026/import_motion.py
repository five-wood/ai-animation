"""Native same-rig Biped/Bone import and actual-Max adaptive key reduction.
Run inside Max 2026. Operates only in the dedicated working directory and reloads
base_scene.max between trials. NEVER loads/resets/overwrites the original file.
Settings are read from import_config.json. No automatic foot-contact inference.
"""
import os,json,math,traceback,time
import pymxs
rt=pymxs.runtime
BASE=r'D:\Project\ai-animation\work\max_editable_20261109'
TRIAL_LOADED=False
OUTPUT_COMMITTED=None


def output_path(name,extension):
    if not isinstance(name,str) or os.path.basename(name)!=name or not name.endswith(extension):
        raise RuntimeError('Invalid output filename: '+repr(name))
    path=os.path.abspath(os.path.join(BASE,name))
    if os.path.normcase(path)==os.path.normcase(BASE+r'\base_scene.max'):
        raise RuntimeError('Cannot overwrite working base')
    return path


def matrix(rows):return rt.matrix3(*[rt.point3(*v) for v in rows])
def rows(m):return [[float(m[i][j]) for j in range(3)] for i in range(4)]
def distance(a,b):return math.sqrt(sum((a[i]-b[i])**2 for i in range(3)))
def angle(a,b):
    # Max row matrices, evaluated orientation. Drivers have unit scale.
    trace=sum(a[i][j]*b[i][j] for i in range(3) for j in range(3))
    return math.degrees(math.acos(max(-1.,min(1.,(trace-1.)/2.))))
def atomic_json(path,data):
    temp=path+'.tmp'
    with open(temp,'w',encoding='utf8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
    os.replace(temp,path)

def key_inventory(names):
    result=[];seen=set()
    def scan(c,path):
        try:h=int(rt.getHandleByAnim(c))
        except:return
        if h in seen:return
        seen.add(h)
        n=int(rt.numKeys(c))
        if n>0:
            result.append({'path':path,'class':str(rt.classOf(c)),'num_keys':n,
              'times_ticks':[int(rt.getKeyTime(c,i).ticks) for i in range(1,n+1)]})
        for i in range(1,int(c.numSubs)+1):
            sub=rt.getSubAnim(c,i)
            try:child=sub.controller
            except:continue
            if child is not None:scan(child,path+'/'+str(rt.getSubAnimName(c,i)))
    for name in names:
        n=rt.getNodeByName(name)
        if n:scan(n.controller,name)
    return result


def run():
    global TRIAL_LOADED,OUTPUT_COMMITTED
    rt.execute('fn v2rSetWorldRotation node q = (in coordsys (transMatrix node.transform.pos) (node.rotation = q))')
    cfg=json.load(open(BASE+r'\import_config.json',encoding='utf8'))
    packet=json.load(open(BASE+r'\motion_packet.json',encoding='utf8'))
    current=os.path.abspath(str(rt.maxFilePath)+str(rt.maxFileName))
    if os.path.normcase(os.path.dirname(current))!=os.path.normcase(BASE):
        raise RuntimeError('Refusing to modify a scene outside working folder: '+current)
    base=BASE+r'\base_scene.max'
    if not os.path.isfile(base):raise RuntimeError('Missing base snapshot')
    if bool(rt.getSaveRequired()):raise RuntimeError('Unsaved working scene changes; refusing reset')
    output=output_path(cfg.get('output','sparse_faithful.max'),'.max')
    report_path=output_path(cfg.get('report','import_report.json'),'.json')
    samples_path=output_path(cfg.get('samples','actual_samples.json'),'.json')
    if report_path==samples_path:raise RuntimeError('Report and samples must differ')
    if os.path.exists(output):raise RuntimeError('Refusing to overwrite output: '+output)
    sourcefps=packet['source_fps'];T=packet['sample_count'];fps=packet['target_fps']
    extra_biped=set(cfg.get('extra_biped_nodes',[]))
    drivers=[b for b in packet['bones'] if b['write'] or b['name'] in extra_biped]
    root=next(b for b in drivers if b['name']=='Bip001')
    targets={b['name']:[matrix(v) for v in b['world_matrix3']] for b in drivers}
    if cfg.get('com_from_pelvis_delta',False):
        pelvis=next(b for b in drivers if b['name']=='Bip001 Pelvis')
        root_ref=matrix(root['reference_matrix3']);pelvis_ref=matrix(pelvis['reference_matrix3'])
        targets['Bip001']=[root_ref*rt.inverse(pelvis_ref)*matrix(v) for v in pelvis['world_matrix3']]
    # Evaluate feet/knees as well as animated drivers; they were not independently
    # solved, but still provide useful fidelity checks of hierarchy propagation.
    refs=[b for b in packet['bones'] if b['write'] or b['name'] in ['Bip001 L Calf','Bip001 R Calf','Bip001 L Foot','Bip001 R Foot']]
    tolpos=float(cfg.get('position_tolerance_cm',.15));tolang=float(cfg.get('angle_tolerance_deg',1.5))
    dense=bool(cfg.get('dense',False));maxposes=int(cfg.get('max_pose_keys',70))
    keys=list(range(T)) if dense else sorted(set([0,T-1]+[int(x) for x in cfg.get('initial_keys',[])]))
    history=[];started=time.time()
    names=[b['name'] for b in drivers]
    for iteration in range(1,100):
        if not rt.loadMaxFile(base,quiet=True):raise RuntimeError('Could not restore working snapshot')
        TRIAL_LOADED=True
        rt.frameRate=fps
        # Keep original range (including accessory keys); source occupies 0..150.
        rt.animationRange=rt.interval(min(0,float(rt.animationRange.start.frame)),max(float(rt.animationRange.end.frame),packet['duration_seconds']*fps))
        nodes={b['name']:rt.getNodeByName(b['name']) for b in packet['bones']}
        bc=nodes['Bip001'].controller
        if bool(bc.figureMode) or bool(bc.footstepMode) or bool(bc.mixerMode) or int(rt.biped.numLayers(bc))!=0:
            raise RuntimeError('Unsupported Biped modes/layers in base snapshot')
        # Existing driver animation must not be silently erased. This source has
        # only pose keys at time0 on Biped; ordinary active Bones have no keys.
        inv=key_inventory(names)
        if any(any(t!=0 for t in tr['times_ticks']) for tr in inv):
            raise RuntimeError('Pre-existing animation on import drivers; refusing replacement')
        for t in keys:
            with pymxs.attime(float(t)*fps/sourcefps):
                tm=targets['Bip001'][t]
                rt.biped.setTransform(nodes['Bip001'],rt.Name('pos'),tm.row4,True)
                rt.biped.setTransform(nodes['Bip001'],rt.Name('rotation'),tm.rotationpart,True)
                for b in drivers:
                    if b['name']=='Bip001':continue
                    n=nodes[b['name']];q=targets[b['name']][t].rotationpart
                    if b['target_class']=='Biped_Object':
                        if cfg.get('set_biped_positions',False):
                            rt.biped.setTransform(n,rt.Name('pos'),targets[b['name']][t].row4,True)
                        rt.biped.setTransform(n,rt.Name('rotation'),q,True)
                    else:
                        if str(rt.classOf(n.controller)).lower()!='prs' or str(rt.classOf(rt.getSubAnim(n.controller,2).controller)).lower()!='euler_xyz':
                            raise RuntimeError('Unsupported ordinary Bone controller: '+b['name'])
                        with pymxs.animate(True):rt.v2rSetWorldRotation(n,rt.inverse(q))
        perbone={b['name']:{'max_position_cm':0.,'max_angle_deg':0.} for b in refs}
        perframe=[];sampled=[]
        for t in range(T):
            err=0.;sample={}
            with pymxs.attime(float(t)*fps/sourcefps):
                for b in refs:
                    actual=rows(nodes[b['name']].transform)
                    want=rows(targets['Bip001'][t]) if b['name']=='Bip001' else b['world_matrix3'][t]
                    ep=distance(actual[3],want[3]);ea=angle(actual,want)
                    m=perbone[b['name']];m['max_position_cm']=max(m['max_position_cm'],ep);m['max_angle_deg']=max(m['max_angle_deg'],ea)
                    err=max(err,ep/tolpos,ea/tolang)
                    sample[b['name']]=actual
            perframe.append(err);sampled.append(sample)
        worst=max(range(T),key=lambda t:perframe[t]);peak=perframe[worst]
        record={'iteration':iteration,'source_key_samples':keys.copy(),'pose_count':len(keys),'normalized_max_error':peak,'worst_sample':worst,'per_bone':perbone}
        history.append(record)
        atomic_json(BASE+r'\import_progress.json',{'status':'evaluating','history':history,'elapsed_seconds':time.time()-started})
        # At keyed samples, any persistent error is a mapping/controller issue,
        # not a missing-key issue. Dense conversion is saved as diagnostic only.
        keyed_peak=max(perframe[t] for t in keys)
        if dense or peak<=1. or len(keys)>=maxposes or keyed_peak>2.:
            passed=peak<=1.
            if not passed and not cfg.get('save_diagnostic',False):
                atomic_json(BASE+r'\import_report.json',{'status':'not_saved_conversion_or_budget_failed','history':history,'keyed_peak':keyed_peak})
                rt.loadMaxFile(base,quiet=True)
                return
            rt.sliderTime=0
            ok=rt.saveMaxFile(output,useNewFile=True,clearNeedSaveFlag=True,quiet=True)
            if not ok:raise RuntimeError('saveMaxFile failed')
            OUTPUT_COMMITTED=output
            inv=key_inventory(names)
            result={'status':'passed_source_fidelity' if passed else 'diagnostic_not_accepted','output':output,
              'mode':'dense_reference' if dense else 'adaptive_pose_keys','source_fps':sourcefps,'target_fps':fps,
              'source_duration_seconds':packet['duration_seconds'],'position_tolerance_cm':tolpos,'angle_tolerance_deg':tolang,
              'contact_status':'not_locked_source_contact_unreviewed','pose_count':len(keys),'key_samples':keys,
              'actual_track_inventory':inv,'history':history,'keyed_peak':keyed_peak,
              'elapsed_seconds':time.time()-started,'note':'Source fidelity only, not motion GT or planted-foot acceptance. Original scene protected.'}
            atomic_json(report_path,result)
            atomic_json(samples_path,{'samples':sampled})
            print('V2R import complete:',result['status'],output,len(keys))
            return
        # Add source frames of strongest reconstruction error, grouped pose keys.
        candidates=sorted((t for t in range(T) if t not in keys),key=lambda t:perframe[t],reverse=True)
        count=min(int(cfg.get('keys_per_iteration',4)),maxposes-len(keys))
        chosen=[]
        for t in candidates:
            if all(abs(t-x)>=2 for x in chosen):chosen.append(t)
            if len(chosen)>=count:break
        if not chosen:raise RuntimeError('No progress in adaptive reduction')
        keys=sorted(set(keys+chosen))
    raise RuntimeError('Iteration limit')

try:
    run()
except Exception:
    error=traceback.format_exc()
    rolled_back=False
    try:
        current=os.path.abspath(str(rt.maxFilePath)+str(rt.maxFileName))
        if TRIAL_LOADED and os.path.normcase(os.path.dirname(current))==os.path.normcase(BASE):
            rolled_back=bool(rt.loadMaxFile(BASE+r'\base_scene.max',quiet=True))
    except Exception:
        error+='\nRollback failure:\n'+traceback.format_exc()
    atomic_json(BASE+r'\import.error.json',{'error':error,'rolled_back_to_working_base':rolled_back,'output_committed':OUTPUT_COMMITTED})
    raise
