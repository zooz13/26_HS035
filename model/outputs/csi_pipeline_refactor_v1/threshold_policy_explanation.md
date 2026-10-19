# Threshold Policy Explanation

The default Stage-1 threshold policy is `balanced_macro`.

## Candidate Thresholds

The detector builds quantile candidates from training scores:

```text
candidates = unique(quantile(scores, linspace(0.005, 0.995, 220)))
```

## Selection Objective

For each candidate threshold:

```text
pred = scores >= threshold
metrics = binary_metrics(y_true, pred)
```

`balanced_macro` uses this key:

```text
key = (-macro_f1, empty_false_alarm_rate, occupied_miss_rate)
```

The minimum key wins:

1. Maximize binary macro F1.
2. If tied, prefer lower empty false alarm rate.
3. If still tied, prefer lower occupied miss rate.

No room4 test labels are used for threshold fitting in the room4-excluded protocol.

