import unittest
import numpy as np
import pandas as pd
from scripts.feature_expansion_experiment import origin_anchor,restore,HISTORY_WEIGHT


class ResidualTests(unittest.TestCase):
    def test_preserve_level_and_allow_decrease(self):
        f=pd.DataFrame({'rolling_mean_3':[40000,10,0],'target_usage':[1,2,3]})
        a=origin_anchor(f,'residual_full')
        np.testing.assert_equal(restore([0,-4,-2],a),[40000,6,0])
        f.target_usage=[999,888,777]
        np.testing.assert_equal(a,origin_anchor(f,'residual_full'))
        np.testing.assert_equal(origin_anchor(f,'direct_full'),[0,0,0])

    def test_unclipped_l1_identity(self):
        y=np.array([25.,4.,0.]);a=np.array([20.,10.,0.]);g=np.array([3.,-5.,2.])
        np.testing.assert_allclose(abs((y-a)-g),abs(y-(a+g)))

    def test_missing_anchor_fails(self):
        with self.assertRaises(ValueError):origin_anchor(pd.DataFrame({'rolling_mean_3':[np.nan]}),'residual_full')

    def test_weight_design(self):
        self.assertEqual(HISTORY_WEIGHT,{'direct_full':1.,'residual_full':1.,'residual_quarter':.25,'residual_recent':0.})
