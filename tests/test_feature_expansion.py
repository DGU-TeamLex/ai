import unittest
from argparse import Namespace
from pathlib import Path
import tempfile
from unittest.mock import patch
import numpy as np
import pandas as pd
from src.modeling.feature_expansion import BEHAVIOR, CALENDAR, PEER, expand
from scripts.feature_expansion_experiment import BASE, ARMS, metric
from scripts import feature_expansion_experiment as experiment
from src.modeling.daily_record_features import DAILY


def fixture(values, dates=None):
    dates = pd.date_range('2025-01-01',periods=len(values),freq='MS') if dates is None else pd.to_datetime(dates)
    return pd.DataFrame(dict(series_segment_id=1,year_month=dates,
        forecast_month=dates+pd.offsets.MonthBegin(1),lag_1=values,target_usage=999.,
        month_end_stock_lag_1=10.,month_end_stock_lag_2=12.,
        inbound_qty_lag_1=2.,inbound_qty_lag_2=1.,rolling_mean_3=5.,
        standard_item_family_id='syringe',standard_item_specification='3ml',standard_item_unit_code='EA'))


class FeaturesTest(unittest.TestCase):
    def test_zero_and_positive_age(self):
        f=expand(fixture([0,0,5,0,0,8]))
        self.assertEqual(f.consecutive_zero_months.tolist(),[1,2,0,1,2,0])
        self.assertTrue(np.isnan(f.months_since_positive.iloc[0]))
        self.assertEqual(f.months_since_positive.iloc[4],2)
        self.assertEqual(f.positive_mean_6.iloc[5],6.5)

    def test_missing_not_zero(self):
        f=expand(fixture([5,np.nan,0]))
        self.assertTrue(np.isnan(f.consecutive_zero_months.iloc[1]))
        self.assertEqual(f.consecutive_zero_months.iloc[2],1)
        self.assertTrue(np.isnan(f.months_since_positive.iloc[2]))

    def test_calendar_gap_resets(self):
        f=expand(fixture([5,0],['2025-01-01','2025-03-01']))
        self.assertTrue(np.isnan(f.months_since_positive.iloc[1]))
        self.assertTrue(np.isnan(f.usage_change_1.iloc[1]))

    def test_no_future_target_information(self):
        raw=fixture([1,2,3,4,5,6,7,8])
        before=expand(raw)
        raw['target_usage']=-500
        raw.loc[7,'lag_1']=99999
        after=expand(raw)
        pd.testing.assert_frame_equal(before.loc[:6,BEHAVIOR+CALENDAR+PEER],after.loc[:6,BEHAVIOR+CALENDAR+PEER])

    def test_peers_exclude_self_and_units(self):
        a=fixture([10]); b=fixture([30]); b['series_segment_id']=2
        c=fixture([900]); c['series_segment_id']=3; c['standard_item_unit_code']='BOX'
        f=expand(pd.concat([a,b,c],ignore_index=True))
        self.assertEqual(f.peer_mean_usage.iloc[0],30)
        self.assertEqual(f.peer_mean_usage.iloc[1],10)
        self.assertTrue(np.isnan(f.peer_mean_usage.iloc[2]))

    def test_target_calendar(self):
        f=expand(fixture([1]))
        self.assertEqual(f.target_days.iloc[0],28)
        self.assertEqual(f.target_weekdays.iloc[0],20)

    def test_feature_contract(self):
        self.assertEqual(len(BASE),51)
        for arm,extra in ARMS.items():
            self.assertEqual(len(BASE+extra),len(set(BASE+extra)))
            self.assertNotIn('target_usage',BASE+extra)

    def test_partial_predictions_rejected(self):
        with self.assertRaises(ValueError):
            metric([1,2],[1,np.nan])
        self.assertEqual(metric([10],[8])['BIAS'],-20)

    def test_fit_selection_and_test_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)
            dates=pd.date_range('2024-01-01','2025-12-01',freq='MS').repeat(10)
            f=pd.DataFrame({c:np.ones(len(dates)) for c in BASE+BEHAVIOR+CALENDAR+PEER+DAILY})
            f['forecast_month']=dates
            f['year_month']=dates-pd.offsets.MonthBegin(1)
            f['target_usage']=10+np.arange(len(dates))%7
            f['historical_training_eligible']=False
            f['item_code']=[str(i%10) for i in range(len(dates))]
            for c in ['institution_code','department']:
                f[c]='a'
            f.to_parquet(out/'prepared.parquet',index=False)
            ref=f.rename(columns={'year_month':'forecast_origin_month'})[
                experiment.KEYS+['forecast_month','target_usage']].copy()
            ref['stock_model_a_usage_only_pred']=12.
            ref['temporal_ensemble_pred']=12.
            ref.to_parquet(out/'reference.parquet',index=False)
            args=Namespace(output=out,reference=out/'reference.parquet')
            with patch.object(experiment,'PARAMS',dict(objective='regression_l1',n_estimators=3,n_jobs=1,verbosity=-1,min_child_samples=2)):
                for fold in experiment.FOLDS:
                    for arm in ARMS:
                        args.fold,args.arm=fold,arm
                        experiment.fit(args)
            experiment.finalize(args)
            self.assertTrue((out/'selection.json').exists())
            self.assertTrue((out/'test_results.json').exists())
            self.assertTrue((out/'결과요약.md').exists())


if __name__=='__main__':
    unittest.main()
