import os,sys,unittest,numpy as np,torch
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2r.depth_order import order_targets,ordering_loss
class DepthTests(unittest.TestCase):
 def setUp(self):self.rig={'keypoints':[{'mirror':1},{'mirror':0},{'mirror':-1}]}
 def test_stable_sign_and_neutral_small_gap(self):
  d=np.zeros((24,2,3));d[:,:,0]=.1;w=np.ones_like(d)*.8
  t=order_targets(self.rig,d,w,24);self.assertTrue(np.all(t['gate']>0))
  d[:,:,0]=.001;t=order_targets(self.rig,d,w,24);self.assertEqual(t['gate'].sum(),0)
 def test_crossing_and_low_confidence_not_forced(self):
  d=np.zeros((24,2,3));d[:,:,0]=.1;d[12,:,0]=-.1;w=np.ones_like(d)*.8
  t=order_targets(self.rig,d,w,24);self.assertTrue(np.all(t['gate'][12]==0))
  w[:,:,1]=.1;t=order_targets(self.rig,d,w,24);self.assertEqual(t['gate'].sum(),0)
 def test_loss_correct_and_wrong_sign_gradient(self):
  d=np.zeros((24,2,3));d[:,:,0]=.1;t=order_targets(self.rig,d,np.ones_like(d),24)
  tt={k:torch.tensor(v,dtype=torch.long if k=='pairs' else torch.float32) for k,v in t.items() if k in ['pairs','sign','margin','gate']}
  z=torch.tensor(d,dtype=torch.float32,requires_grad=True);self.assertEqual(float(ordering_loss(z,tt,1)),0)
  zz=torch.tensor(-d,dtype=torch.float32,requires_grad=True);l=ordering_loss(zz,tt,1);self.assertGreater(float(l),0);l.backward();self.assertTrue(torch.isfinite(zz.grad).all())
 def test_neutral_no_pairs(self):
  t=order_targets({'keypoints':[{'mirror':-1}]},np.zeros((1,2,1)),np.ones((1,2,1)),24)
  self.assertEqual(t['gate'].shape,(1,2,0));z=torch.zeros(1,2,1,requires_grad=True);ordering_loss(z,{'pairs':torch.empty(0,2,dtype=torch.long)},1).backward()
 def test_nonfinite_is_not_valid_evidence(self):
  d=np.ones((24,2,3));d[:,:,0]=np.nan;t=order_targets(self.rig,d,np.ones_like(d),24);self.assertEqual(t['gate'].sum(),0)
if __name__=='__main__':unittest.main()
