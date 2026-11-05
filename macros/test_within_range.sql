-- Generic test: fails if any value in the column is outside [min_value, max_value].
-- Like every dbt test, it returns the bad rows; zero rows means a pass.

{% test within_range(model, column_name, min_value, max_value) %}

select {{ column_name }}
from {{ model }}
where {{ column_name }} < {{ min_value }}
   or {{ column_name }} > {{ max_value }}

{% endtest %}
