-- One row per hour: the demand to predict, plus features for a day-ahead forecast.
-- Rule: every feature uses only data from at least 24 hours before the hour,
-- because that is all a day-ahead forecaster would have.

with hourly as (
    select
        date_trunc('hour', interval_start) as hour_start,
        -- Average, not sum: MW is a rate, so the hourly mean is the hour's
        -- average power (and equals the energy used, in MWh).
        avg(demand_mw) as demand_mw,
        count(*) as n_intervals
    from {{ ref('stg_price_and_demand') }}
    group by 1
),

with_features as (
    select
        hour_start,
        demand_mw,
        n_intervals,

        -- Lags. Counting back in rows is safe because the gap test proves
        -- there are no missing intervals, so one row is always one hour.
        lag(demand_mw, 24) over w as lag_24h,
        lag(demand_mw, 168) over w as lag_168h,

        -- Rolling means that end 24 hours back, so they never see the last day.
        avg(demand_mw) over (w rows between 47 preceding and 24 preceding) as rolling_mean_24h,
        avg(demand_mw) over (w rows between 191 preceding and 24 preceding) as rolling_mean_168h,
        -- Window functions average whatever rows exist, so early rows would
        -- get a mean of fewer hours. Count them so we can null those out.
        count(demand_mw) over (w rows between 47 preceding and 24 preceding) as n_hours_in_24h_window,
        count(demand_mw) over (w rows between 191 preceding and 24 preceding) as n_hours_in_168h_window
    from hourly
    window w as (order by hour_start)
),

calendar as (
    select
        *,
        -- Market time is AEST all year; Melbourne clocks move to AEDT in summer.
        -- 'Etc/GMT-10' means UTC+10 (the sign is reversed in this naming scheme).
        timezone('Australia/Melbourne', timezone('Etc/GMT-10', hour_start)) as local_hour_start
    from with_features
)

select
    calendar.hour_start,
    calendar.demand_mw,
    calendar.n_intervals,

    calendar.lag_24h,
    calendar.lag_168h,
    case when calendar.n_hours_in_24h_window = 24 then calendar.rolling_mean_24h end as rolling_mean_24h,
    case when calendar.n_hours_in_168h_window = 168 then calendar.rolling_mean_168h end as rolling_mean_168h,

    extract(hour from calendar.hour_start) as hour_of_day,
    extract(hour from calendar.local_hour_start) as local_hour_of_day,
    extract(isodow from calendar.hour_start) as day_of_week,  -- 1 = Monday, 7 = Sunday
    extract(isodow from calendar.hour_start) in (6, 7) as is_weekend,
    extract(month from calendar.hour_start) as month,
    holidays.holiday_date is not null as is_public_holiday
from calendar
left join {{ ref('vic_public_holidays') }} as holidays
    on cast(calendar.hour_start as date) = holidays.holiday_date
order by calendar.hour_start
