---
name: cost_alert
subject: "[Store] spend update - {{ month_spend }} USD"
variables: [month_spend, monthly_cap]
---
Hello,

Spend this month: {{ month_spend }} USD of {{ monthly_cap }} USD.
