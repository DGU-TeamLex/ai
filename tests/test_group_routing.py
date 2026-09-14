import importlib.util
from pathlib import Path
import unittest
import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location('routing',Path(__file__).resolve().parents[1]/'scripts/group_routing_experiment.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class RoutingTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame(dict(institution_code=['a']*4,department=['d']*4,
            standard_item_family_id=['syringe']*4,standard_item_unit_code=['EA','EA','',''],
            forecast_month=pd.to_datetime(['2025-05-01']*4),rolling_mean_3=[3.,1.,2.,2.],
            rolling_std_3=[1.]*4, history_months=[8]*4,target_usage=[1.]*4))

    def test_conservation_and_unknown_fallback(self):
        f=self.frame()
        p,n=m.reconcile(f,np.array([4.,4.,7.,9.]))
        np.testing.assert_allclose(p,[6.,2.,7.,9.])
        self.assertEqual(n,2)

    def test_no_outcome_dependency(self):
        f=self.frame()
        p=np.array([4.,4.,7.,9.])
        w={'default':.25}
        a=m.route(f,p,w); b=m.reconcile(f,p)[0]
        f.target_usage=999999
        np.testing.assert_allclose(a,m.route(f,p,w))
        np.testing.assert_allclose(b,m.reconcile(f,p)[0])

    def test_units_and_months_not_mixed(self):
        f=self.frame()
        f.loc[1,'forecast_month']=pd.Timestamp('2025-06-01')
        p=np.array([4.,4.,7.,9.])
        np.testing.assert_allclose(p,m.reconcile(f,p)[0])

    def test_frozen_fallback(self):
        f=self.frame(); f.target_usage=f.rolling_mean_3
        w=m.learn_weights(f,np.zeros(4))
        self.assertEqual(w['default'],1.)
        np.testing.assert_allclose(m.route(f,np.zeros(4),w),f.rolling_mean_3)


if __name__=='__main__':
    unittest.main()
