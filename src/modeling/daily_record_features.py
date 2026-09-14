"""Origin-month recorded outbound activity. Closing dates are not clinical dates."""
import json
from pathlib import Path
import time
import duckdb
import numpy as np
import pandas as pd
import psutil

KEYS=['institution_code','department','item_code']
DAILY=['recorded_days','positive_recorded_days','positive_daily_mean','daily_max',
       'positive_daily_std','outbound_last7','outbound_last14','positive_days_last7',
       'positive_days_last14','last7_share','days_since_last_positive_in_month',
       'invalid_quantity_records','negative_quantity_records']
RAW=['보건기관코드_en','부서코드','물품코드','재고마감일','정상출고량']


def normalize(chunk):
    f=pd.DataFrame({k:chunk[r].astype(str).str.strip() for k,r in zip(KEYS,RAW)})
    f['date']=pd.to_datetime(chunk['재고마감일'],format='%Y%m%d',errors='coerce')
    numeric=pd.to_numeric(chunk['정상출고량'],errors='coerce').replace([np.inf,-np.inf],np.nan)
    f['positive_qty']=numeric.clip(lower=0).fillna(0)
    f['invalid_qty']=numeric.isna().astype('int32')
    f['negative_qty']=numeric.lt(0).astype('int32')
    valid=f.date.notna() & f[KEYS].ne('').all(axis=1)
    return f.loc[valid].copy(),int((~valid).sum())


def aggregate_sql():
    return '''WITH daily AS (
      SELECT institution_code,department,item_code,date,
        sum(positive_qty) qty,sum(invalid_qty) invalid,sum(negative_qty) negative
      FROM records GROUP BY ALL
    ), dated AS (
      SELECT *,date_trunc('month',date)::DATE year_month,
        date_diff('day',date,last_day(date)) days_to_end FROM daily
    ) SELECT institution_code,department,item_code,year_month,
      sum(qty) raw_positive_sum,count(*) recorded_days,
      count(*) FILTER (WHERE qty>0) positive_recorded_days,
      avg(qty) FILTER (WHERE qty>0) positive_daily_mean,max(qty) daily_max,
      stddev_samp(qty) FILTER (WHERE qty>0) positive_daily_std,
      sum(CASE WHEN days_to_end<7 THEN qty ELSE 0 END) outbound_last7,
      sum(CASE WHEN days_to_end<14 THEN qty ELSE 0 END) outbound_last14,
      count(*) FILTER (WHERE qty>0 AND days_to_end<7) positive_days_last7,
      count(*) FILTER (WHERE qty>0 AND days_to_end<14) positive_days_last14,
      sum(CASE WHEN days_to_end<7 THEN qty ELSE 0 END)/nullif(sum(qty),0) last7_share,
      min(days_to_end) FILTER (WHERE qty>0) days_since_last_positive_in_month,
      sum(invalid) invalid_quantity_records,sum(negative) negative_quantity_records
      FROM dated GROUP BY institution_code,department,item_code,year_month'''


def build(root,out):
    files=sorted((root/'raw_stock').glob('*.DAT'))
    if not files: raise FileNotFoundError('No raw DAT files')
    db_path=out/'daily_records.duckdb'
    if db_path.exists(): raise FileExistsError(db_path)
    db=duckdb.connect(str(db_path))
    db.execute("SET threads=2")
    db.execute("SET memory_limit='1GB'")
    db.execute("SET preserve_insertion_order=false")
    db.execute('CREATE TABLE records (institution_code VARCHAR, department VARCHAR,item_code VARCHAR,date TIMESTAMP,positive_qty DOUBLE,invalid_qty INTEGER,negative_qty INTEGER)')
    manifest=[]
    try:
        for path in files:
            count=dropped=0
            print(f'Reading raw source: {path.name}',flush=True)
            for chunk in pd.read_csv(path,sep='|',encoding='utf-8-sig',dtype=str,keep_default_na=False,usecols=RAW,chunksize=50000):
                if psutil.virtual_memory().available<2*1024**3: raise RuntimeError('Low memory during daily preparation')
                part,invalid=normalize(chunk)
                count+=len(chunk)
                dropped+=invalid
                # All origin months retained; each feature aggregates only within its own month.
                db.register('part',part)
                db.execute('INSERT INTO records SELECT * FROM part')
                db.unregister('part')
                time.sleep(.02)
            manifest.append(dict(path=str(path),bytes=path.stat().st_size,mtime_ns=path.stat().st_mtime_ns,records=count,invalid_keys_or_dates=dropped))
        target=out/'daily_monthly.parquet'
        db.execute('COPY ('+aggregate_sql()+') TO ? (FORMAT PARQUET)',[str(target)])
        audit=dict(files=manifest,monthly_rows=db.execute('SELECT count(*) FROM read_parquet(?)',[str(target)]).fetchone()[0],
                   date_semantics='Recorded closing dates, not confirmed patient visits or actual usage dates',
                   duplicate_policy='Aggregate same series/date across every source before counting dates; preserve source quantities; no deduplication of indistinguishable records')
        with (out/'daily_source_manifest.json').open('x',encoding='utf-8') as h: json.dump(audit,h,ensure_ascii=False,indent=2)
    finally:
        db.close()


def attach(frame,daily):
    f=frame.copy(deep=False)
    d=daily.copy(deep=False)
    for c in KEYS:
        categories=pd.Index(frame[c].astype('string').unique()).union(pd.Index(daily[c].astype('string').unique()))
        dtype=pd.CategoricalDtype(categories=categories)
        f[c]=frame[c].astype('string').astype(dtype)
        d[c]=daily[c].astype('string').astype(dtype)
    d['year_month']=pd.to_datetime(d.year_month)
    f=f.merge(d,on=KEYS+['year_month'],how='left',validate='many_to_one',indicator=True)
    if not f['_merge'].eq('both').all(): raise ValueError('Missing daily origin-month coverage')
    if not np.isclose(f.model_demand_positive_sum,f.raw_positive_sum,rtol=1e-5,atol=1e-4).all():
        raise ValueError('Daily positive totals do not match source monthly demand; do not train')
    for c in DAILY: f[c]=f[c].astype('float32')
    return f.drop(columns=['_merge','raw_positive_sum'])
