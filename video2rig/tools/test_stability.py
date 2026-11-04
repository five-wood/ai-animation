import os,sys,unittest
import numpy as np
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2r.stabilize import regularize, continuous_quats, stabilize_arrays

class StabilityTests(unittest.TestCase):
    def test_constants_lines(self):
        for t in (1,2,3,9,121):
            y=np.arange(t,dtype=float)[:,None]*np.array([[1.,-2.,3.]])+4
            np.testing.assert_allclose(regularize(y,24,1.5), y, atol=1e-8)
    def test_frequency_response_and_phase(self):
        t=np.arange(240)/24
        y=np.sin(2*np.pi*.5*t)
        z=regularize(y,24,1.5)
        np.testing.assert_allclose(z[24:-24],y[24:-24],atol=.02)
        high=np.sin(2*np.pi*6*t)
        self.assertLess(np.linalg.norm(regularize(high,24,1.5)[24:-24])/np.linalg.norm(high[24:-24]),.02)
        pulse=np.exp(-((np.arange(121)-60)/12)**2)
        self.assertEqual(np.argmax(regularize(pulse,24,1.5)),60)
    def test_sign_continuity(self):
        q=np.array([[1.,0,0,0],[-1.,0,0,0],[1.,0,0,0]])
        np.testing.assert_allclose(continuous_quats(q),np.tile([1,0,0,0],(3,1)))
    def test_validation(self):
        with self.assertRaises(ValueError): regularize(np.ones(20),24,12)
        with self.assertRaises(ValueError): regularize(np.array([np.nan]),24,2)

if __name__=='__main__':unittest.main()
