from pathlib import Path
import tempfile
import unittest
import duckdb
import pandas as pd
from src.modeling.daily_record_features import normalize,aggregate_sql,attach,build,DAILY,RAW


def fixture():
    return pd.DataFrame([['01','dept','item',d,q] for d,q in [('20250101','10'),('20250125','5'),('20250125','2'),('20250131','-9'),('20250131','bad'),('20250201','999')]],columns=RAW)


class DailyTests(unittest.TestCase):
    def test_dates_negative_missing_and_future(self):
        f,dropped=normalize(fixture())
        self.assertEqual(dropped,0)
        with duckdb.connect() as db:
            db.register('records',f)
            monthly=db.execute(aggregate_sql()).df().sort_values('year_month')
        jan=monthly.iloc[0]
        self.assertEqual(jan.raw_positive_sum,17)
        self.assertEqual(jan.recorded_days,3)
        self.assertEqual(jan.positive_recorded_days,2)
        self.assertEqual(jan.outbound_last7,7)
        self.assertEqual(jan.positive_days_last7,1)
        self.assertEqual(jan.invalid_quantity_records,1)
        self.assertEqual(jan.negative_quantity_records,1)
        self.assertEqual(jan.days_since_last_positive_in_month,6)
        self.assertEqual(jan.daily_max,10)
        frame=pd.DataFrame(dict(institution_code=['01'],department=['dept'],item_code=['item'],year_month=pd.to_datetime(['2025-01-01']),model_demand_positive_sum=[17],target_usage=[999]))
        merged=attach(frame,monthly)
        self.assertEqual(merged.outbound_last7.iloc[0],7)
        frame.target_usage=-1000
        pd.testing.assert_frame_equal(merged[DAILY],attach(frame,monthly)[DAILY])
        frame.model_demand_positive_sum=18
        with self.assertRaises(ValueError): attach(frame,monthly)
        frame.year_month=pd.to_datetime(['2025-03-01'])
        with self.assertRaises(ValueError): attach(frame,monthly)

    def test_split_files_combine_dates(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'raw_stock').mkdir()
            out=root/'out'; out.mkdir()
            f=fixture()
            f.iloc[:2].to_csv(root/'raw_stock/a.DAT',sep='|',index=False,encoding='utf-8-sig')
            f.iloc[2:].to_csv(root/'raw_stock/b.DAT',sep='|',index=False,encoding='utf-8-sig')
            build(root,out)
            jan=pd.read_parquet(out/'daily_monthly.parquet').sort_values('year_month').iloc[0]
            self.assertEqual(jan.positive_recorded_days,2)
            self.assertEqual(jan.raw_positive_sum,17)
            with self.assertRaises(FileExistsError): build(root,out)

    def test_invalid_keys_rejected(self):
        f=fixture()
        f.loc[0,'재고마감일']='invalid'
        f.loc[1,'물품코드']=''
        out,dropped=normalize(f)
        self.assertEqual(dropped,2)
        self.assertEqual(len(out),4)
