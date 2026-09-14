import importlib.util
from pathlib import Path
import unittest
import numpy as np
import pandas as pd

spec=importlib.util.spec_from_file_location('audit',Path(__file__).resolve().parents[1]/'scripts/audit_group_stability.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class StabilityTests(unittest.TestCase):
    def fixture(self):
        rows=[]
        for i in ['a','b']:
            for t,date in enumerate(pd.date_range('2024-11-01',periods=6,freq='MS')):
                rows.append(dict(institution_code='x',department='d',item_code=i,standard_item_family_id='f',
                    standard_item_unit_code='EA',year_month=date,model_demand_positive_sum=(t+1 if i=='a' else 6-t),_valid=True))
        return pd.DataFrame(rows)

    def test_cancellation_not_individual_stability(self):
        f,months=m.balanced_groups(self.fixture(),'2025-04-01')
        row=m.stability(f,months)[0]
        self.assertAlmostEqual(row['group_cv'],0)
        self.assertGreater(row['weighted_item_cv'],0)
        self.assertAlmostEqual(row['cv_reduction_fraction'],1)

    def test_missing_is_not_zero(self):
        f=self.fixture().iloc[1:].copy()
        out,_=m.balanced_groups(f,'2025-04-01')
        self.assertEqual(len(out),0)

    def test_future_does_not_change_selection(self):
        f=self.fixture(); extra=f.iloc[[0]].copy();extra.year_month=pd.Timestamp('2025-05-01')
        a,_=m.balanced_groups(f,'2025-04-01');b,_=m.balanced_groups(pd.concat([f,extra]),'2025-04-01')
        self.assertEqual(len(a),len(b))

    def test_unit_change_excluded(self):
        f=self.fixture();f.loc[0,'standard_item_unit_code']='BOX'
        a,_=m.balanced_groups(f,'2025-04-01')
        self.assertEqual(len(a),0)

    def test_allocation_and_nonnegative(self):
        a=np.array([[1,2,3,4,5,6],[6,5,4,3,2,1]],float)
        predictions=m.forecast(a)
        for value in predictions.values():
            self.assertTrue(np.isfinite(value).all())
            self.assertTrue((value>=0).all())
            self.assertAlmostEqual(value.sum(),7)
        np.testing.assert_allclose(predictions['item_mean3'],a[:,-3:].mean(axis=1))

    def test_unknown_metadata(self):
        f=self.fixture();f.loc[0,'standard_item_unit_code']='unknown'
        self.assertFalse(m.valid_metadata(f).iloc[0])


if __name__=='__main__':unittest.main()
