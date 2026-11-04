"""Integration regression using lizard's existing stable squat (no source mutation)."""
import os,sys,json,unittest
import numpy as np
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__)) ))
from v2r.contact import contact_refine
from v2r.geom import Skeleton

class ContactTest(unittest.TestCase):
    def test_late_contact_preserves_other_motion(self):
        p='/data/video2rig/projects/lizard'
        with open(p+'/rig.json') as f:rig=json.load(f)
        with np.load(p+'/out/xiyi2_stable/motion.npz') as z:motion={k:z[k].copy() for k in z.files}
        sk=Skeleton(rig)
        result,report=contact_refine(rig,motion,iters=1000)
        allowed=[i for i,b in enumerate(sk.active) if rig['bones'][b]['name'] in report['modified_bones']]
        other=[i for i in range(sk.A) if i not in allowed]
        np.testing.assert_array_equal(result['G'],motion['G'])
        np.testing.assert_array_equal(result['eul'][:,other],motion['eul'][:,other])
        np.testing.assert_array_equal(result['eul'][:73],motion['eul'][:73])
        self.assertTrue(np.all(result['eul']>=sk.limits[sk.active,:,0]-1e-6))
        self.assertTrue(np.all(result['eul']<=sk.limits[sk.active,:,1]+1e-6))
        self.assertLess(report['after_contact']['step_p90_pct_size'],.01)
        self.assertLess(report['after_contact']['anchor_error_p90_pct_size'],.01)
        self.assertGreater(report['before_contact']['step_p90_pct_size'],.1)

if __name__=='__main__':unittest.main()
