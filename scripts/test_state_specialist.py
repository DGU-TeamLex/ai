import unittest
import pandas as pd
from state_specialist import states,specialist_masks


class Tests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame(dict(history_months=[2,8,8,8,8],lag_1=[0,0,2,2,2],
           rolling_mean_3=[0,2,2,2,2],rolling_std_3=[0,1,.2,1,2],
           year_month=pd.to_datetime(['2025-03-01']*5),
           forecast_month=pd.to_datetime(['2025-04-01']*5),historical_training_eligible=[False]*5))

    def test_states_and_precedence(self):
        self.assertEqual(states(self.frame()).tolist(),['short_history','last_zero','stable','moderate','volatile'])

    def test_no_target_dependency(self):
        f=self.frame();old=states(f);f['target_usage']=[99999,0,100,1,10]
        self.assertEqual(old.tolist(),states(f).tolist())

    def test_train_validation_disjoint(self):
        f=self.frame();f.loc[3,'forecast_month']=pd.Timestamp('2025-05-01')
        tr,va=specialist_masks(f,['2025-04-01','2025-05-01','2025-06-01'],'moderate')
        self.assertFalse(tr.any());self.assertEqual(va.tolist(),[False,False,False,True,False])
        self.assertFalse((tr&va).any())


if __name__=='__main__':unittest.main()
