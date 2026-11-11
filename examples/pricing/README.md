# Dated API-equivalent scenarios

[2026-11-08 Standard Sol](gpt-6.1-sol-standard-2026-11-08.json) records the
[official API pricing](https://developers.openai.com/api/docs/pricing) checked on
that date: per million text tokens, $2 uncached input, $0.10 cached input and
$10 output. This schedule applies only when every request has at most 272,000
input tokens. It estimates a comparison in dollars; it is not an observed cash
charge for Codex subscription use.

The official longer-context Standard rates are $4/$0.20/$15 for the entire
request above that threshold. This example's flat-rate estimator leaves cost
unknown when any request exceeds its bound or complete broker request sizes are
unavailable. A large session total alone does not select the long-context tier.
Fast service costs twice Standard; regional processing adds 10%. Neither is
included in this Standard scenario. Cache writes, tools and infrastructure are
also excluded; missing cache-write telemetry does not establish zero charges.
Keep these limitations beside any reported dollar estimate.

The [2026-10-28 scenario](gpt-6.1-sol-standard-2026-10-28.json) is retained for
earlier frozen plans. Choose and freeze the dated scenario explicitly; do not
rewrite old plans or receipts when public prices change. The
[accounting contract](../../docs/system/ACCOUNTING.md) owns token partitions,
unknowns and exclusions.
