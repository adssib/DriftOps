## Policies (Brier, lower is better)

| policy | retrains | promoted | blocked | 2019 control | 2020 Jan-Feb | 2020 Mar-Apr shock | 2020 May-Jun empty skies | all |
|---|---|---|---|---|---|---|---|---|
| never | 0 | 0 | 0 | 0.1603 | 0.1412 | 0.2446 | 0.1067 | 0.1640 |
| monthly_8w_nogate | 18 | 18 | 0 | 0.1693 | 0.1497 | 0.2474 | 0.0869 | 0.1711 |
| monthly_8w_gated | 18 | 13 | 0 | 0.1670 | 0.1497 | 0.2420 | 0.0869 | 0.1689 |
| monthly_52w_gated | 18 | 3 | 0 | 0.1603 | 0.1412 | 0.2394 | 0.0933 | 0.1629 |
| driftops_8w | 11 | 10 | 0 | 0.1603 | 0.1412 | 0.2303 | 0.0853 | 0.1617 |
| driftops_52w | 14 | 11 | 0 | 0.1603 | 0.1412 | 0.2339 | 0.0875 | 0.1621 |

## Corruption: distance in km for 2 weeks from 2019-09-03

| policy | retrains | promoted | blocked | corruption + 8 weeks | 2019 control | all |
|---|---|---|---|---|---|---|
| corrupt_never | 0 | 0 | 0 | 0.1371 | 0.1604 | 0.1641 |
| corrupt_driftops_guard | 11 | 10 | 20 | 0.1371 | 0.1604 | 0.1618 |
| corrupt_driftops_noguard | 14 | 11 | 0 | 0.1480 | 0.1633 | 0.1661 |
