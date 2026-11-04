"""Cheap tests of heatmap evidence, identity metadata alignment and temporal math."""
import os,sys,unittest
import numpy as np
import torch
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from v2r.model import heatmap_quality
from v2r.solve import fix_swaps,triangulate_all
from v2r.geom import Cams

class TemporalIntegrationTests(unittest.TestCase):
    def test_second_peak_ambiguity(self):
        ys,xs=torch.meshgrid(torch.arange(25),torch.arange(25),indexing='ij')
        p=torch.exp(-((xs-8.)**2+(ys-12.)**2)/4.)*.9+.0001
        second=torch.exp(-((xs-19.)**2+(ys-12.)**2)/4.)*.85+.0001
        one=torch.logit(p.clamp(.00001,.99999))[None,None]
        two=torch.logit(torch.maximum(p,second).clamp(.00001,.99999))[None,None]
        a,b=heatmap_quality(one),heatmap_quality(two)
        self.assertLess(float(a['ambiguity_ratio']),.01)
        self.assertGreater(float(b['ambiguity_ratio']),.9)
        self.assertTrue(torch.isfinite(a['spread_heatmap_px']).all())
    def test_default_swap_api_and_metadata(self):
        import json
        project='/data/video2rig/projects/lizard'
        with open(project+'/rig.json') as f:rig=json.load(f)
        with open(project+'/studio.json') as f:studio=json.load(f)
        with open(project+'/exam/frames/motion_gt.json') as f:gt=json.load(f)
        uv=np.array([[r[c['name']]['uvz'] for c in studio['cams']] for r in gt['frames']])[...,:2]
        conf=np.full(uv.shape[:-1],.9)
        cams=Cams(studio)
        legacy=fix_swaps(cams,rig,uv,conf)
        new=fix_swaps(cams,rig,uv,conf,return_permutation=True)
        self.assertEqual(len(legacy),3)
        np.testing.assert_array_equal(legacy[0],new[0])
        raw=uv.copy()
        # Force many left/right limb identity errors, then check each piece of metadata
        # follows the exact same permutation regardless of whether geometry fixes every one.
        from v2r.solve import limb_chains
        for L,R in limb_chains(rig):
            raw[20:25,1,L],raw[20:25,1,R]=uv[20:25,1,R],uv[20:25,1,L]
        fixed,_,_,perm=fix_swaps(cams,rig,raw,conf,return_permutation=True)
        reconstructed=np.take_along_axis(raw,perm[...,None],axis=2)
        np.testing.assert_array_equal(reconstructed,fixed)
        meta=np.broadcast_to(np.arange(uv.shape[2]),conf.shape)
        aligned=np.take_along_axis(meta,perm,axis=2)
        np.testing.assert_array_equal(aligned,perm)

if __name__=='__main__':unittest.main()
