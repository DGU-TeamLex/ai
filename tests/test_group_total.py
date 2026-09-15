import importlib.util
from pathlib import Path
import unittest
import numpy as np
import pandas as pd

s=importlib.util.spec_from_file_location('exp',Path(__file__).resolve().parents[1]/'scripts/group_total_experiment.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)


class Tests(unittest.TestCase):
    def test_allocation_preserves_total(self):
        p=m.allocation([2,3,0,0],[1,1,1,1],[0,0,1,1],[20,20,10,10],'model')
        np.testing.assert_allclose(p,[8,12,5,5])

    def test_zero_total(self):
        np.testing.assert_allclose(m.allocation([2,3],[1,1],[0,0],[0,0],'model'),[0,0])

    def test_leaf_error_not_aggregate_error(self):
        self.assertEqual(m.score([10,10],[0,20])['WAPE'],100)
        self.assertEqual(m.score([20],[20])['WAPE'],0)

    def test_group_target_not_feature(self):
        self.assertNotIn('target_usage',m.FEATURES)

    def test_groups_block_unknown_and_missing_history(self):
        f=pd.DataFrame({c:['A']*4 for c in m.GROUP})
        f['item_code']=['1','2','3','4']
        f['forecast_month']=pd.Timestamp('2025-05-01')
        for c in m.SUM:f[c]=1.
        f['history_months']=6;f['rolling_std_3']=1.
        f['target_usage']=2.;f['historical_training_eligible']=True
        g,mp=m.group_frame(f)
        self.assertEqual(g.member_count.tolist(),[4])
        self.assertEqual(g.target_usage.tolist(),[8])
        f.loc[0,'lag_6']=np.nan
        self.assertEqual(len(m.group_frame(f)[0]),0)
        f['standard_item_unit_code']='UNKNOWN'
        self.assertEqual(len(m.group_frame(f)[0]),0)


if __name__=='__main__':unittest.main()
