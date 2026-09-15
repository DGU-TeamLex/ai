import importlib.util
from pathlib import Path
import unittest
import numpy as np
import pandas as pd
s=importlib.util.spec_from_file_location('search',Path(__file__).resolve().parents[1]/'scripts/histboost_search.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

class Tests(unittest.TestCase):
    def test_scale_preserves_absolute_loss(self):
        f=pd.DataFrame({'rolling_mean_3':[0,10,np.nan,100]})
        scale=m.scale(f,'scaled');y=np.array([2,3,4,5]);p=np.array([1,2,3,4])
        np.testing.assert_allclose(scale*abs(y/scale-p),abs(y-scale*p))
        self.assertTrue((scale>=1).all())
    def test_training_only_encoding(self):
        f=pd.DataFrame({'c':['A','A','B'],'n':[1.,2.,3.]})
        b=m.fit_encoder(f,['c','n']);x,cat=m.encode(pd.DataFrame({'c':['NEW',None],'n':[1,np.inf]}),b)
        self.assertEqual(x.c.iloc[0],253);self.assertTrue(np.isnan(x.c.iloc[1]))
        self.assertEqual(x.c__frequency.tolist(),[0,0]);self.assertEqual(cat,['c'])
    def test_category_limit(self):
        f=pd.DataFrame({'c':[str(i) for i in range(400)]})
        x,_=m.encode(f,m.fit_encoder(f,['c']))
        self.assertLessEqual(x.c.nunique(),254)
    def test_estimator_external_validation(self):
        from sklearn.ensemble import HistGradientBoostingRegressor
        f=pd.DataFrame({'c':['A','B']*50,'n':np.arange(100.)})
        b=m.fit_encoder(f,['c','n']);x,_=m.encode(f,b)
        model=HistGradientBoostingRegressor(loss='absolute_error',max_iter=2,categorical_features=[True,False,False],early_stopping=True)
        model.fit(x.iloc[:80],np.arange(80.),X_val=x.iloc[80:],y_val=np.arange(80.,100.),sample_weight=np.ones(80),sample_weight_val=np.ones(20))
        self.assertTrue(np.isfinite(model.predict(x.iloc[80:])).all())

if __name__=='__main__':unittest.main()
