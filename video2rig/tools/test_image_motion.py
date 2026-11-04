"""Conservative stationarity regression; no GPU and no trained reader required."""
import sys,os,unittest
import numpy as np,cv2
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__)) ))
from v2r.image_motion import stationary_evidence,stationary_segments

class ImageMotionTests(unittest.TestCase):
    def frames(self,move=False,blank=False,fps=24):
        rng=np.random.default_rng(42)
        base=np.zeros((96,128),np.uint8) if blank else rng.integers(0,255,(96,128),dtype=np.uint8)
        base=cv2.GaussianBlur(base,(3,3),0)
        T=int(fps)+1
        panels=[];proj=np.zeros((T,1,1,2))
        for t in range(T):
            dx=t*12/fps if move else 0
            image=cv2.warpAffine(base,np.float32([[1,0,dx],[0,1,0]]),(128,96),borderMode=cv2.BORDER_REFLECT)
            panels.append([cv2.cvtColor(image,cv2.COLOR_GRAY2BGR)])
            proj[t,0,0]=[64+dx,48]
        return panels,proj
    def test_static_texture_supported(self):
        p,x=self.frames();_,d=stationary_evidence(p,x,24,radius=25)
        starts,w,ints=stationary_segments(d,24)
        self.assertGreater(w.mean(),.6)
        self.assertGreater(len(ints),0)
    def test_moving_texture_not_locked(self):
        for fps in [24,48]:
            p,x=self.frames(move=True,fps=fps);_,d=stationary_evidence(p,x,fps,radius=25)
            _,w,ints=stationary_segments(d,fps)
            self.assertEqual(len(ints),0)
            self.assertEqual(w.max(),0)
    def test_blank_no_fake_evidence(self):
        p,x=self.frames(blank=True);f,d=stationary_evidence(p,x,24)
        _,w,ints=stationary_segments(d,24)
        self.assertEqual(w.max(),0)
        self.assertTrue(np.isfinite(d['flow_speed_px_per_second']).all())
    def test_cumulative_drift_not_locked(self):
        speed=np.full((100,1,1),.5)
        _,w,ints=stationary_segments({'flow_speed_px_per_second':speed,'flow_support_count':np.ones_like(speed)*10},24)
        self.assertEqual(len(ints),0) # 2px cumulative drift despite instant speed<1px/s

if __name__=='__main__':unittest.main()
