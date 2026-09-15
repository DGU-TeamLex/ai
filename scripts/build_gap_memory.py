"""Keep stale segment memory separate from calendar lags; never impute missing months."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

ITEM=['institution_code','department','item_code']
META=['standard_item_definition_key','standard_item_unit_code']
EXTRA=['prior_segment_last','prior_segment_mean','prior_segment_count','prior_segment_age_months']


def features(source):
    f=source.sort_values(ITEM+['year_month']).reset_index(drop=True).copy()
    if f.duplicated(ITEM+['year_month']).any():raise ValueError('Duplicate item-month')
    for c in ITEM+META:f[c]=f[c].astype('string').fillna('').str.strip()
    same=f[ITEM].eq(f[ITEM].shift()).all(axis=1)
    same_meta=f[META].eq(f[META].shift()).all(axis=1)
    consecutive=f.year_month.eq(f.year_month.shift()+pd.offsets.MonthBegin())
    y=pd.to_numeric(f.model_demand_positive_sum,errors='coerce')
    good=np.isfinite(y)&y.ge(0)
    seg=(~same|~same_meta|~consecutive|~good|~good.shift(fill_value=False)).cumsum()
    f['_seg']=seg
    f['_y']=y.where(good)
    summary=f.groupby('_seg',sort=False).agg(**{c:(c,'first') for c in ITEM+META},
        end=('year_month','max'),last=('_y','last'),mean=('_y','mean'),count=('_y','count'))
    previous=summary.shift()
    compatible=summary[ITEM+META].eq(previous[ITEM+META]).all(axis=1)
    known=summary[META].ne('').all(axis=1)
    for c in META:known &= ~summary[c].str.contains('UNKNOWN|UNRESOLVED|UNSPECIFIED|MISSING|^NAN$|^NONE$',case=False,regex=True)
    compatible &= known & previous['count'].gt(0)
    prior=previous[['end','last','mean','count']].where(compatible)
    prior.columns=['_end']+EXTRA[:3]
    out=f[ITEM+['year_month','_seg']].join(prior,on='_seg')
    age=(out.year_month.dt.year-out._end.dt.year)*12+out.year_month.dt.month-out._end.dt.month
    available=age.between(2,12)
    out[EXTRA[:3]]=out[EXTRA[:3]].where(available)
    out[EXTRA[3]]=age.where(available)
    return out[ITEM+['year_month']+EXTRA]


def run(a):
    a.output.mkdir(parents=True,exist_ok=False)
    (a.output/'status.json').write_text('{"stage":"preparing"}',encoding='utf-8')
    try:
        raw=pd.read_parquet(a.history,columns=ITEM+META+['year_month','model_demand_positive_sum'])
        extra=features(raw)
        f=pd.read_parquet(a.prepared)
        original=f.columns.tolist()
        for c in ITEM:extra[c]=extra[c].astype(str)
        # Join copies avoid changing original categorical representations.
        lookup=f[ITEM+['year_month']].copy()
        for c in ITEM:lookup[c]=lookup[c].astype(str)
        joined=lookup.merge(extra,on=ITEM+['year_month'],how='left',validate='one_to_one',sort=False)
        if len(joined)!=len(f) or not lookup.reset_index(drop=True).equals(joined[lookup.columns].reset_index(drop=True)):raise ValueError('Order changed')
        for c in EXTRA:f[c]=joined[c].to_numpy(dtype='float32')
        dest=a.output/'prepared.parquet';f.to_parquet(dest,index=False)
        if not pd.read_parquet(dest,columns=original).equals(pd.read_parquet(a.prepared)):raise ValueError('Original values changed')
        valid=f.forecast_month.isin(pd.to_datetime(['2025-05-01','2025-06-01','2025-08-01','2025-09-01']))
        report=dict(rows=len(f),rows_with_memory=int(f[EXTRA[0]].notna().sum()),validation_rows=int(valid.sum()),
            validation_with_memory=int((valid & f[EXTRA[0]].notna()).sum()),features=EXTRA,
            original_columns_verified=True,history_source=str(a.history.resolve()),prepared_source=str(a.prepared.resolve()),
            prepared_sha256=hashlib.sha256(a.prepared.read_bytes()).hexdigest(),
            policy='Immediately previous segment only, exact item/definition/unit; age 2..12 months; no zero imputation',
            new_model_trained=False)
        (a.output/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (a.output/'status.json').write_text('{"stage":"completed"}',encoding='utf-8')
    except Exception as e:
        (a.output/'status.json').write_text(json.dumps(dict(stage='failed',error=repr(e))),encoding='utf-8');raise


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for c in ['history','prepared','output']:p.add_argument('--'+c,type=Path,required=True)
    run(p.parse_args())
