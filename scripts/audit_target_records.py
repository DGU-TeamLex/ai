"""Read-only post-hoc target audit. Never remove samples or train on audit labels."""
import argparse
import json
import os
from pathlib import Path
import time

os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
import numpy as np
import pandas as pd
import psutil

KEYS=['institution_code','department','item_code']
RAW_KEYS=['보건기관코드_en','부서코드','물품코드']


def save(path, value):
    with path.open('x',encoding='utf-8') as handle:
        json.dump(value,handle,ensure_ascii=False,indent=2,allow_nan=False,default=str)


def join_monthly(predictions, monthly):
    p=predictions.copy()
    m=monthly.copy()
    for c in KEYS:
        p[c]=p[c].astype(str)
        m[c]=m[c].astype(str)
    m=m.rename(columns={'year_month':'forecast_month'})
    f=p.merge(m,on=KEYS+['forecast_month'],how='left',validate='one_to_one',indicator=True)
    if not f['_merge'].eq('both').all():
        raise ValueError('Unmatched target-month records')
    f['abs_error']=(f.prediction-f.target_usage).abs()
    return f.drop(columns='_merge')


def raw_summary(rows):
    if not rows:
        return dict(records=0)
    f=pd.DataFrame(rows)
    signed=pd.to_numeric(f['정상출고량'],errors='coerce')
    positive=signed.clip(lower=0).fillna(0)
    by_date=positive.groupby(f['재고마감일']).sum()
    raw_cols=[c for c in f if not c.startswith('_')]
    return dict(records=len(f),distinct_dates=int(f['재고마감일'].nunique()),
        positive_dates=int(by_date.gt(0).sum()),positive_sum=float(positive.sum()),
        signed_sum=float(signed.sum()),negative_records=int(signed.lt(0).sum()),
        invalid_numeric_records=int(signed.isna().sum()),
        largest_date_share=float(by_date.max()/positive.sum()) if positive.sum()>0 else None,
        duplicate_payload_records=int(f.duplicated(raw_cols).sum()),
        # Identical payloads are a review signal, not proof of duplicate transactions.
        sources=sorted(set(f['_source'])))


def run(root,out):
    out.mkdir(parents=True,exist_ok=False)
    pred_path=root/'.teamlex_git/feature-expansion-20260914/outputs/feature_expansion_v1/test_behavior_predictions.parquet'
    pred=pd.read_parquet(pred_path)
    monthly_path=root/'data/processed/stock_monthly.parquet'
    m=pd.read_parquet(monthly_path,filters=[('year_month','>=',pd.Timestamp('2025-09-01')),('year_month','<=',pd.Timestamp('2025-12-01'))])
    f=join_monthly(pred,m)
    # Metadata belongs to the forecast origin, not the target month.
    feature_path=root/'outputs/stock_feature_table.parquet'
    origin=pd.read_parquet(feature_path,columns=KEYS+['year_month','standard_item_unit_code','history_months','rolling_mean_3'],
        filters=[('year_month','>=',pd.Timestamp('2025-09-01')),('year_month','<=',pd.Timestamp('2025-11-01'))])
    for c in KEYS: origin[c]=origin[c].astype(str)
    origin=origin.rename(columns={'year_month':'forecast_origin_month'})
    f=f.merge(origin,on=KEYS+['forecast_origin_month'],validate='one_to_one',how='left',indicator=True)
    if not f['_merge'].eq('both').all(): raise ValueError('Unmatched origin metadata')
    f=f.drop(columns='_merge').sort_values('abs_error',ascending=False,kind='stable').reset_index(drop=True)
    total_error=float(f.abs_error.sum())
    top_n=int(np.ceil(len(f)*.05))
    masks={
        'target_positive_sum_mismatch':~np.isclose(f.target_usage,f.model_demand_positive_sum,rtol=1e-5,atol=1e-4),
        'target_negative_normal_outbound':f.negative_normal_outbound_count.gt(0),
        'target_physical_violation':f.ledger_physical_violation_count.gt(0),
        'target_balance_violation':f.ledger_balance_violation_count.gt(0),
        'target_missing_opening':f.ledger_opening_stock_missing_count.gt(0),
        'target_transfer_present':f.transfer_in_qty.ne(0)|f.transfer_out_qty.ne(0),
        'target_returns_present':f.return_in_qty.ne(0)|f.return_out_qty.ne(0),
        'target_correction_present':f.correction_out_qty.ne(0),
        'target_auto_disposal_present':f.auto_disposal_adjustment_qty.ne(0),
        'origin_history_under_6':f.history_months.lt(6),
        'target_over_twice_origin_mean':f.target_usage.gt(2*f.rolling_mean_3)&f.rolling_mean_3.gt(0),
    }
    def group(mask):
        mask=pd.Series(np.asarray(mask,dtype=bool),index=f.index)
        return dict(rows=int(mask.sum()),row_pct=float(100*mask.mean()),
            error_pct=float(100*f.loc[mask,'abs_error'].sum()/total_error),
            top5_flag_pct=float(100*mask.iloc[:top_n].mean()),rest_flag_pct=float(100*mask.iloc[top_n:].mean()))
    result=dict(rows=len(f),WAPE=100*total_error/float(f.target_usage.sum()),
        BIAS=100*float((f.prediction-f.target_usage).sum()/f.target_usage.sum()),
        flags={name:group(mask) for name,mask in masks.items()},
        units={str(unit):dict(rows=len(g),error_pct=float(100*g.abs_error.sum()/total_error),actual_sum=float(g.target_usage.sum())) for unit,g in f.groupby('standard_item_unit_code',observed=True)},
        source_files={str(p):dict(bytes=p.stat().st_size,mtime_ns=p.stat().st_mtime_ns) for p in [pred_path,monthly_path,feature_path]},
        limitations=['Post-hoc reused test, not causal inference or future predictors',
            'High-error and matched controls are purposive, not representative random samples',
            'Ledger violation can reflect timing/semantics, not necessarily erroneous data',
            'No unit conversion; no sample exclusion; raw closing date not verified usage date'])
    save(out/'aggregate.json',result)
    # Twenty distinct high-error series, then typical-error matched controls.
    chosen=f.drop_duplicates(KEYS).head(20).copy()
    chosen['audit_group']='high_error'
    used=set(chosen.index)
    controls=[]
    bins=np.floor(np.log10(f.target_usage.clip(lower=1)))
    for index,row in chosen.iterrows():
        mask=f.forecast_month.eq(row.forecast_month)&f.standard_item_unit_code.eq(row.standard_item_unit_code)&bins.eq(bins.loc[index])&~f.index.isin(used)&(f.index>=top_n)
        candidates=f.loc[mask].sort_values('abs_error')
        if len(candidates):
            control=candidates.iloc[len(candidates)//2].copy()
            control['audit_group']='matched_typical'
            used.add(control.name)
            controls.append(control)
    cases=pd.concat([chosen,pd.DataFrame(controls)],ignore_index=True)
    selected={}
    case_records=[]
    for i,row in cases.iterrows():
        case_id=f'C{i+1:02d}'
        record={c:row[c] for c in KEYS+['forecast_month','forecast_origin_month','prediction','target_usage','abs_error','audit_group','standard_item_unit_code','history_months','rolling_mean_3']}
        record['case_id']=case_id
        for role,date in [('target',row.forecast_month),('origin',row.forecast_origin_month)]:
            key=tuple(str(row[c]).strip() for c in KEYS)+(pd.Timestamp(date).strftime('%Y%m'),)
            selected.setdefault(key,[]).append((case_id,role))
        case_records.append(record)
    save(out/'private_case_keys.json',case_records)
    wanted_series={key[:3] for key in selected}
    buckets={key:[] for key in selected}
    files=sorted((root/'raw_stock').glob('익스포트_*.DAT'))
    if not files: raise FileNotFoundError('Current raw DAT files not found')
    scanned=[]
    for path in files:
        ordinal=0
        print(f'Scanning {path.name}',flush=True)
        for chunk in pd.read_csv(path,sep='|',encoding='utf-8-sig',dtype=str,keep_default_na=False,chunksize=20000):
            if psutil.virtual_memory().available<2*1024**3: raise RuntimeError('Low memory; audit stopped without restarting')
            chunk.columns=chunk.columns.str.strip()
            keys=list(zip(*(chunk[c].str.strip() for c in RAW_KEYS)))
            indices=[i for i,key in enumerate(keys) if key in wanted_series]
            for i in indices:
                row=chunk.iloc[i].to_dict()
                key=keys[i]+(row['재고마감일'][:6],)
                if key in buckets:
                    row.update(_source=path.name,_record_ordinal=ordinal+i+1)
                    buckets[key].append(row)
            ordinal+=len(chunk)
            time.sleep(.02)
        scanned.append(dict(file=path.name,records=ordinal,bytes=path.stat().st_size,mtime_ns=path.stat().st_mtime_ns))
    raw_cases={r['case_id']:{} for r in case_records}
    with (out/'private_raw_records.jsonl').open('x',encoding='utf-8') as handle:
        for key,rows in buckets.items():
            summary=raw_summary(rows)
            for case_id,role in selected[key]: raw_cases[case_id][role]=summary
            for row in rows: handle.write(json.dumps(dict(matches=selected[key],record=row),ensure_ascii=False)+'\n')
    for record in case_records:
        details=raw_cases[record['case_id']]
        details['target_matches_prediction_target']=bool(np.isclose(details['target'].get('positive_sum',np.nan),record['target_usage'],rtol=1e-5,atol=1e-4))
        details['audit_group']=record['audit_group']
        details['actual']=float(record['target_usage'])
        details['prediction']=float(record['prediction'])
    save(out/'raw_case_summaries.json',raw_cases)
    save(out/'scan_manifest.json',scanned)
    save(out/'completed.json',dict(completed=True,cases=len(cases),scanned_records=sum(x['records'] for x in scanned)))
    print('Audit completed',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    run(a.source,a.output)
