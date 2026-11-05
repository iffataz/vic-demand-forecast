-- Singular test: returns every expected 5-minute interval that is missing.
-- Builds the full list of intervals from the first to the last we have,
-- then keeps the ones with no matching row. Zero rows means no gaps.

with bounds as (
    select min(interval_start) as first_start, max(interval_start) as last_start
    from {{ ref('stg_price_and_demand') }}
),

expected as (
    select unnest(generate_series(first_start, last_start, interval '5 minutes')) as interval_start
    from bounds
)

select expected.interval_start as missing_interval_start
from expected
left join {{ ref('stg_price_and_demand') }} as actual
    on expected.interval_start = actual.interval_start
where actual.interval_start is null
