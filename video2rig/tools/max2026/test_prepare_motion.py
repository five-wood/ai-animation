"""Local NumPy regression checks; never imports pymxs or touches Max."""
import json,unittest
from pathlib import Path
import numpy as np
from prepare_motion import BASE,col,qmat

class MotionPacketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet=json.loads((BASE/'work/max_editable_20261109/motion_packet.json').read_text(encoding='utf8'))
        cls.rig=json.loads((BASE/'work/maxkeys/m11101/rig.json').read_text(encoding='utf8'))
        cls.motion=np.load(BASE/'work/maxkeys/m11101/motion_temporal.npz',allow_pickle=False)
    def test_time_mapping(self):
        t=np.arange(self.packet['sample_count'])/self.packet['source_fps']
        np.testing.assert_allclose(t*4800,np.arange(121)*200)
        self.assertEqual(t[-1],5.)
        self.assertEqual(t[-1]*self.packet['target_fps'],150.)
    def test_zero_motion_rest_delta(self):
        W=np.array(self.rig['world_matrix']);C=np.diag([100,100,100,1])
        for source,target in zip(self.rig['bones'],self.packet['bones']):
            if not target['write']:continue
            s=W@np.array(source['rest']);ref=col(target['reference_matrix3'])
            out=C@s@np.linalg.inv(s)@np.linalg.inv(C)@ref
            np.testing.assert_allclose(out,ref,atol=1e-10)
    def test_driver_rigid_and_rest_match(self):
        for b in self.packet['bones']:
            if not b['write']:continue
            self.assertLess(b['reference_head_error_cm'],.02)
            R=np.array(b['world_matrix3'])[:,:3,:]
            np.testing.assert_allclose(R@R.transpose(0,2,1),np.broadcast_to(np.eye(3),R.shape),atol=1e-10)
            np.testing.assert_allclose(np.linalg.det(R),1.,atol=1e-10)
    def test_quaternion_sign_equivalence(self):
        q=self.motion['quat']
        np.testing.assert_allclose(qmat(q),qmat(-q),atol=1e-12)
    def test_source_fk_checked(self):
        self.assertLess(self.packet['max_fk_reference_error_source_units'],1e-5)
        self.assertEqual(sum(b['write'] for b in self.packet['bones']),23)
    def test_inactive_bones_not_written(self):
        for b in self.packet['bones']:
            self.assertEqual(b['write'],b['source_active'] or b['name']=='Bip001')

if __name__=='__main__':unittest.main()
