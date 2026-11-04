"""Create compact reproducible tables from the audit; local only."""
import json,csv,math
from pathlib import Path
p=Path(__file__).parent;r=json.loads((p/'results/audit.json').read_text());K=r['metadata']['kp_count'];B=r['metadata']['bone_count'];size=r['metadata']['rig_size']
lines=[]
def put(*x):lines.append(' '.join(map(str,x)))
put('size',size,'K',K,'B',B)
for key in ['uv_reader','uv','kp_world','kp_body']:
    a=r['reading_aggregate'][key];a=a.get('spectrum',a);norm=math.sqrt(2*K) if key.startswith('uv') else math.sqrt(K)*size/100
    put(key, 'ge3/4/6 normalized RMS',*[round(a[f'ge{c}_rms']/norm,5) for c in [3,4,6]],'pct',*[round(a[f'ge{c}_pct'],4) for c in [3,4,6]])
put('global center',json.dumps(r['global']['center_translation']))
put('\nLOCAL/WORLD ROTATIONS name speedRMS accRMS accMAX waccRMS >=3/4/6velocity% local3HzRMSdeg')
for b in sorted(r['bones'],key=lambda x:x['local']['acc_deg_s2']['rms'],reverse=True):
    l=b['local'];w=b['world'];put(b['name'],*[round(x,3) for x in [l['speed_deg_s']['rms'],l['acc_deg_s2']['rms'],l['acc_deg_s2']['max'],w['acc_deg_s2']['rms'],l['velocity_deg_s']['ge3_pct'],l['velocity_deg_s']['ge4_pct'],l['velocity_deg_s']['ge6_pct'],l['trajectory_deg']['ge3_rms']]])
put('\nKEYPOINTS world>=3HzRMS(%size) body>=3HzRMS(%size) accRMS(%size/s2) UV(front/side)>=3RMSpx CONF(front/side) reprj(front/side)')
for k in sorted(r['keypoints'],key=lambda x:x['world']['position']['ge3_energy'],reverse=True):
    put(k['name'],*[round(x,4) for x in [k['world']['position']['ge3_rms']/size*100,k['body']['position']['ge3_rms']/size*100,k['world']['acc']['rms']/size*100]],'uv',*[round(v['uv']['position']['ge3_rms'],3) for v in k['views']],'conf',*[round(v['conf']['median'],3) for v in k['views']],'reproj',*[round(v['reprojection_px']['median'],3) for v in k['views']])
put('\nATTRIBUTION component deltaHF RMS(%size) observedHFafter/original energyRatio maximum_kp_and_deltaRMS(%size)')
orig=r['reading_aggregate']['kp_world']['ge3_energy']
for a in sorted(r['counterfactual_3Hz'],key=lambda x:x['delta_spectrum']['ge3_energy'],reverse=True):
    ks=sorted(a['delta_per_kp_rms_pct_size'].items(),key=lambda t:t[1],reverse=True)[:3]
    put(a['component'],round(a['delta_spectrum']['ge3_rms']/math.sqrt(K)/size*100,5),round(a['after_position_spectrum']['ge3_energy']/orig,4),ks)
put('\nFLOW largest')
for v in r['video']['flow_check']:
    put(v['view'],'aggregate',v['reader_vs_LK_step_residual_px'])
    for k in sorted(v['per_kp'],key=lambda x:x['residual_px']['rms'] if x['residual_px'] else 0,reverse=True)[:7]:put(k)
(p/'results/compact_tables.txt').write_text('\n'.join(lines),encoding='utf-8')
with (p/'results/bones.csv').open('w',newline='',encoding='utf-8-sig') as f:
    w=csv.writer(f);w.writerow(['bone','active','local_speed_RMS_deg_s','local_acc_RMS_deg_s2','local_acc_max_deg_s2','world_acc_RMS_deg_s2','local_velocity_ge3_pct','local_velocity_ge4_pct','local_velocity_ge6_pct','local_trajectory_ge3_RMS_deg'])
    for b in r['bones']:
        l=b['local'];w.writerow([b['name'],b['active'],l['speed_deg_s']['rms'],l['acc_deg_s2']['rms'],l['acc_deg_s2']['max'],b['world']['acc_deg_s2']['rms'],*[l['velocity_deg_s'][f'ge{c}_pct'] for c in [3,4,6]],l['trajectory_deg']['ge3_rms']])
with (p/'results/keypoints.csv').open('w',newline='',encoding='utf-8-sig') as f:
    w=csv.writer(f);w.writerow(['keypoint','world_HF3_RMS_pct_size','body_HF3_RMS_pct_size','world_acc_RMS_pct_size_s2','front_uv_HF3_RMS_px','side_uv_HF3_RMS_px','front_conf_median','side_conf_median','front_reproj_median_px','side_reproj_median_px'])
    for k in r['keypoints']:w.writerow([k['name'],k['world']['position']['ge3_rms']/size*100,k['body']['position']['ge3_rms']/size*100,k['world']['acc']['rms']/size*100,*[v['uv']['position']['ge3_rms'] for v in k['views']],*[v['conf']['median'] for v in k['views']],*[v['reprojection_px']['median'] for v in k['views']]])
