-- One row per 5-minute interval: typed, renamed and deduplicated.
-- Timestamps stay in market time (AEST, UTC+10, no daylight saving).

with source as (
    select * from {{ source('aemo', 'price_and_demand') }}
),

typed as (
    select
        strptime(SETTLEMENTDATE, '%Y/%m/%d %H:%M:%S') as interval_end,
        cast(TOTALDEMAND as double) as demand_mw,
        cast(RRP as double) as price_aud_mwh,
        REGION as region,
        filename as source_file
    from source
),

deduplicated as (
    -- Keep one row per interval in case AEMO ever publishes an interval twice.
    select *
    from typed
    qualify row_number() over (partition by interval_end order by source_file) = 1
)

select
    -- AEMO stamps the END of each interval, so 01:00 covers 00:55 to 01:00.
    -- interval_start puts every row in the hour it actually belongs to.
    interval_end - interval '5 minutes' as interval_start,
    interval_end,
    demand_mw,
    price_aud_mwh,
    region,
    source_file
from deduplicated
