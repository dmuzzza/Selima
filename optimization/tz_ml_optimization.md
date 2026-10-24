====================================================
 OPTUNA OPTIMIZATION RESULTS
====================================================
 Strategy          : macd_plus
 Instruments       : 36
 Total trials      : 4320
 Objective metric  : sharpe_ratio
 Best combination  : CHMF (BBG00475K6C3) | 1d
 Best Sharpe (IS)  : 2.62

 BEST PARAMETERS:
  macd_fast_period   : 5
  macd_slow_period   : 21
  macd_signal_period : 16
  trend_ma_period    : 198
  tp_percent         : 1.1
  sl_percent         : 1.7

====================================================
 IN-SAMPLE METRICS (IS, 70%)
====================================================
  Total trades    : 20
  Closed trades   : 20
  Win Rate        : 85.0%
  Profit Factor   : 4.65
  Sharpe Ratio    : 2.62
  Expectancy      : +1.98%
  Max Drawdown    : 3.5%
  Payoff Ratio    : 0.89
  Total PnL       : +472897 RUB (+47.29%)

====================================================
 OUT-OF-SAMPLE METRICS (OOS, 30%) ← главная проверка
====================================================
  Total trades    : 6
  Closed trades   : 6
  Win Rate        : 83.3%
  Profit Factor   : 4.66
  Sharpe Ratio    : 3.03
  Expectancy      : +1.76%
  Max Drawdown    : 3.0%
  Payoff Ratio    : 0.91
  Total PnL       : +108739 RUB (+10.87%)

 ✅ OOS Sharpe > 0, PF > 1.0 — стратегия жизнеспособна
====================================================

====================================================
 DETAILED RESULTS BY INSTRUMENT
====================================================

CHMF (BBG00475K6C3) | 1d
  IS Sharpe: 2.62 | OOS Sharpe: 3.03
  IS PF:     4.65 | OOS PF:     4.66
  Params: MACD(5,21,16), MA198, TP=1.1%, SL=1.7%

OZON (TCS80A10CW95) | 4h
  IS Sharpe: 2.41 | OOS Sharpe: -0.91
  IS PF:     2.99 | OOS PF:     0.71
  Params: MACD(20,37,19), MA71, TP=3.4%, SL=1.0%

OZON (TCS80A10CW95) | 1d
  IS Sharpe: 2.14 | OOS Sharpe: -1.54
  IS PF:     4.40 | OOS PF:     0.44
  Params: MACD(9,32,9), MA84, TP=2.1%, SL=1.4%

EUTR (TCS00A1002V2) | 1d
  IS Sharpe: 2.10 | OOS Sharpe: 3.84
  IS PF:     2.78 | OOS PF:     3.45
  Params: MACD(4,12,9), MA175, TP=6.0%, SL=4.0%

CHMF (BBG00475K6C3) | 4h
  IS Sharpe: 2.05 | OOS Sharpe: 0.10
  IS PF:     1.82 | OOS PF:     1.01
  Params: MACD(11,12,9), MA265, TP=3.2%, SL=3.7%

AFKS (BBG004S68614) | 1d
  IS Sharpe: 1.96 | OOS Sharpe: -1.75
  IS PF:     2.76 | OOS PF:     0.51
  Params: MACD(6,11,6), MA270, TP=0.9%, SL=1.8%

PIKK (BBG004S68BH6) | 4h
  IS Sharpe: 1.88 | OOS Sharpe: -1.53
  IS PF:     2.00 | OOS PF:     0.64
  Params: MACD(15,25,20), MA259, TP=5.3%, SL=1.7%

AFKS (BBG004S68614) | 4h
  IS Sharpe: 1.61 | OOS Sharpe: -0.77
  IS PF:     1.66 | OOS PF:     0.80
  Params: MACD(5,31,11), MA92, TP=4.9%, SL=0.4%

MGNT (BBG004RVFCY3) | 4h
  IS Sharpe: 1.50 | OOS Sharpe: -1.38
  IS PF:     1.65 | OOS PF:     0.68
  Params: MACD(9,18,11), MA51, TP=5.2%, SL=3.8%

MOEX (BBG004730JJ5) | 1d
  IS Sharpe: 1.34 | OOS Sharpe: -2.46
  IS PF:     2.13 | OOS PF:     0.29
  Params: MACD(4,29,14), MA143, TP=1.7%, SL=2.6%

EUTR (TCS00A1002V2) | 4h
  IS Sharpe: 1.33 | OOS Sharpe: 0.67
  IS PF:     1.89 | OOS PF:     1.25
  Params: MACD(16,54,21), MA72, TP=5.3%, SL=2.0%

SBER (BBG004730N88) | 1d
  IS Sharpe: 1.29 | OOS Sharpe: -3.52
  IS PF:     1.90 | OOS PF:     0.00
  Params: MACD(4,14,7), MA138, TP=3.2%, SL=2.3%

MGNT (BBG004RVFCY3) | 1d
  IS Sharpe: 1.28 | OOS Sharpe: 1.21
  IS PF:     2.04 | OOS PF:     1.65
  Params: MACD(3,36,11), MA249, TP=5.9%, SL=1.8%

POSI (TCS00A103X66) | 1d
  IS Sharpe: 1.23 | OOS Sharpe: 0.56
  IS PF:     2.17 | OOS PF:     1.42
  Params: MACD(10,19,13), MA53, TP=6.0%, SL=0.6%

LKOH (BBG004731032) | 4h
  IS Sharpe: 1.20 | OOS Sharpe: 0.62
  IS PF:     1.69 | OOS PF:     1.18
  Params: MACD(18,46,13), MA50, TP=4.5%, SL=3.5%

GAZP (BBG004730RP0) | 4h
  IS Sharpe: 1.16 | OOS Sharpe: 0.51
  IS PF:     1.70 | OOS PF:     1.19
  Params: MACD(18,42,21), MA83, TP=4.6%, SL=1.6%

NVTK (BBG00475KKY8) | 1d
  IS Sharpe: 1.07 | OOS Sharpe: -0.36
  IS PF:     2.08 | OOS PF:     0.76
  Params: MACD(13,14,12), MA71, TP=3.3%, SL=3.5%

MOEX (BBG004730JJ5) | 4h
  IS Sharpe: 1.02 | OOS Sharpe: -1.09
  IS PF:     1.48 | OOS PF:     0.68
  Params: MACD(10,37,13), MA55, TP=1.3%, SL=1.6%

FLOT (BBG000R04X57) | 4h
  IS Sharpe: 0.98 | OOS Sharpe: -0.77
  IS PF:     1.48 | OOS PF:     0.75
  Params: MACD(20,41,21), MA264, TP=5.2%, SL=1.3%

POSI (TCS00A103X66) | 4h
  IS Sharpe: 0.98 | OOS Sharpe: 0.60
  IS PF:     1.44 | OOS PF:     1.19
  Params: MACD(10,46,19), MA145, TP=4.4%, SL=3.0%

GAZP (BBG004730RP0) | 1d
  IS Sharpe: 0.94 | OOS Sharpe: 1.33
  IS PF:     2.07 | OOS PF:     2.68
  Params: MACD(3,30,20), MA167, TP=4.0%, SL=2.8%

NVTK (BBG00475KKY8) | 4h
  IS Sharpe: 0.93 | OOS Sharpe: -0.91
  IS PF:     1.27 | OOS PF:     0.79
  Params: MACD(4,49,4), MA263, TP=4.9%, SL=3.2%

SNGSP (BBG004S681M2) | 1d
  IS Sharpe: 0.92 | OOS Sharpe: -1.07
  IS PF:     1.71 | OOS PF:     0.54
  Params: MACD(5,14,8), MA107, TP=2.5%, SL=3.0%

VTBR (BBG004730ZJ9) | 1d
  IS Sharpe: 0.88 | OOS Sharpe: -2.20
  IS PF:     1.86 | OOS PF:     0.29
  Params: MACD(8,14,17), MA104, TP=4.4%, SL=0.8%

PIKK (BBG004S68BH6) | 1d
  IS Sharpe: 0.82 | OOS Sharpe: -1.06
  IS PF:     1.66 | OOS PF:     0.52
  Params: MACD(8,31,9), MA109, TP=1.5%, SL=2.6%

AFLT (BBG004S683W7) | 1d
  IS Sharpe: 0.77 | OOS Sharpe: -0.05
  IS PF:     1.57 | OOS PF:     0.94
  Params: MACD(5,25,16), MA53, TP=1.9%, SL=1.3%

T (TCS80A107UL4) | 1d
  IS Sharpe: 0.74 | OOS Sharpe: 32.69
  IS PF:     1.42 | OOS PF:     0.00
  Params: MACD(8,13,3), MA257, TP=5.9%, SL=0.3%

T (TCS80A107UL4) | 4h
  IS Sharpe: 0.68 | OOS Sharpe: -0.98
  IS PF:     1.23 | OOS PF:     0.69
  Params: MACD(8,55,6), MA177, TP=3.1%, SL=2.1%

FLOT (BBG000R04X57) | 1d
  IS Sharpe: 0.67 | OOS Sharpe: -1.65
  IS PF:     1.37 | OOS PF:     0.50
  Params: MACD(3,38,5), MA286, TP=3.5%, SL=1.5%

LKOH (BBG004731032) | 1d
  IS Sharpe: 0.64 | OOS Sharpe: -1.26
  IS PF:     1.44 | OOS PF:     0.54
  Params: MACD(5,42,3), MA61, TP=4.2%, SL=0.8%

X5 (TCS03A108X38) | 4h
  IS Sharpe: 0.63 | OOS Sharpe: -2.30
  IS PF:     1.34 | OOS PF:     0.41
  Params: MACD(21,46,18), MA227, TP=2.5%, SL=2.5%

X5 (TCS03A108X38) | 1d
  IS Sharpe: 0.54 | OOS Sharpe: -0.69
  IS PF:     1.41 | OOS PF:     0.45
  Params: MACD(3,33,9), MA145, TP=2.7%, SL=2.3%

VTBR (BBG004730ZJ9) | 4h
  IS Sharpe: 0.41 | OOS Sharpe: -2.02
  IS PF:     1.11 | OOS PF:     0.56
  Params: MACD(7,22,10), MA93, TP=3.6%, SL=1.0%

SBER (BBG004730N88) | 4h
  IS Sharpe: 0.27 | OOS Sharpe: -2.42
  IS PF:     1.10 | OOS PF:     0.36
  Params: MACD(17,49,10), MA90, TP=2.0%, SL=3.4%

AFLT (BBG004S683W7) | 4h
  IS Sharpe: 0.22 | OOS Sharpe: -2.03
  IS PF:     1.05 | OOS PF:     0.59
  Params: MACD(7,29,12), MA151, TP=0.7%, SL=2.3%

SNGSP (BBG004S681M2) | 4h
  IS Sharpe: -0.02 | OOS Sharpe: -0.12
  IS PF:     0.97 | OOS PF:     0.94
  Params: MACD(18,29,14), MA296, TP=0.6%, SL=3.1%
