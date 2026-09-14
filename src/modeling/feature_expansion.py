"""Origin-time features for local experiments, not a serving pipeline change."""
import numpy as np
import pandas as pd


BEHAVIOR = ['months_since_positive', 'never_positive', 'consecutive_zero_months',
            'positive_mean_6', 'positive_count_6', 'recent_minus_previous_3',
            'usage_change_1', 'stock_cover_months', 'stock_change_1', 'inbound_change_1']
CALENDAR = ['target_days', 'target_weekdays', 'target_weekend_days']
PEER = ['peer_mean_usage', 'peer_positive_share', 'peer_count']


def expand(frame):
    """lag_1 is origin-month usage. Never read target_usage to make features.

    Break histories at missing calendar months and invalid usage. Peer values
    require an observed common origin month, family, specification and unit;
    leave the current item out. No claim that units are converted or items
    sharing metadata are clinically interchangeable.
    """
    f = frame.sort_values(['series_segment_id', 'year_month']).reset_index(drop=True).copy()
    if f.duplicated(['series_segment_id', 'year_month']).any():
        raise ValueError('Duplicate segment/month')
    changed = f.series_segment_id.ne(f.series_segment_id.shift())
    gap = f.year_month.ne(f.year_month.shift() + pd.offsets.MonthBegin(1))
    invalid = f.lag_1.isna() | f.lag_1.lt(0)
    group = (changed | gap | invalid | invalid.shift(fill_value=False)).cumsum()
    y = f.lag_1.where(~invalid)
    position = f.groupby(group, sort=False).cumcount()
    last_positive = position.where(y.gt(0)).groupby(group).ffill()
    f['months_since_positive'] = position - last_positive
    f['never_positive'] = last_positive.isna().astype('float32').where(~invalid)
    # A zero run can precede the first positive observation; missing != zero.
    zero_group = (group.ne(group.shift()) | ~y.eq(0)).cumsum()
    f['consecutive_zero_months'] = y.eq(0).astype(int).groupby(zero_group).cumsum().where(~invalid)
    def rolling(values, window, operation):
        r = values.groupby(group, sort=False).rolling(window, min_periods=1)
        return getattr(r, operation)().reset_index(level=0, drop=True).reindex(f.index)
    f['positive_mean_6'] = rolling(y.where(y.gt(0)), 6, 'mean')
    f['positive_count_6'] = rolling(y.gt(0).astype(float).where(y.notna()), 6, 'sum')
    previous_3 = rolling(y.groupby(group).shift(3), 3, 'mean')
    f['recent_minus_previous_3'] = rolling(y, 3, 'mean') - previous_3
    f['usage_change_1'] = y - y.groupby(group).shift()
    f['stock_cover_months'] = f.month_end_stock_lag_1 / f.rolling_mean_3.where(f.rolling_mean_3.gt(0))
    f['stock_change_1'] = f.month_end_stock_lag_1 - f.month_end_stock_lag_2
    f['inbound_change_1'] = f.inbound_qty_lag_1 - f.inbound_qty_lag_2
    dates = pd.to_datetime(f.forecast_month)
    f['target_days'] = dates.dt.days_in_month
    unique = dates.drop_duplicates()
    weekdays = {d: int(np.busday_count(d.date(), (d + pd.offsets.MonthBegin(1)).date())) for d in unique}
    f['target_weekdays'] = dates.map(weekdays)
    f['target_weekend_days'] = f.target_days - f.target_weekdays
    keys = ['year_month', 'standard_item_family_id', 'standard_item_specification', 'standard_item_unit_code']
    # Unknown classification cannot form a meaningful peer group.
    known = pd.Series(True, index=f.index)
    for col in keys[1:]:
        text = f[col].astype('string').str.strip().str.lower()
        known &= text.notna() & ~text.isin(['', 'unknown', 'unmatched', 'nan', 'none'])
    peer_y = y.where(known)
    grouped = peer_y.groupby([f[c] for c in keys], observed=True, dropna=False)
    count = grouped.transform('count') - peer_y.notna().astype(int)
    total = grouped.transform('sum') - peer_y.fillna(0)
    positive = peer_y.gt(0).astype(float).where(peer_y.notna())
    positive_total = positive.groupby([f[c] for c in keys], observed=True, dropna=False).transform('sum') - positive.fillna(0)
    f['peer_count'] = count.where(known)
    f['peer_mean_usage'] = (total / count.where(count.gt(0))).where(known)
    f['peer_positive_share'] = (positive_total / count.where(count.gt(0))).where(known)
    for col in BEHAVIOR + CALENDAR + PEER:
        f[col] = f[col].replace([np.inf, -np.inf], np.nan).astype('float32')
    return f
