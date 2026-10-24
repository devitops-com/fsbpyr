"""statsmodels: formula API (patsy), GLM, ARIMA and a dataset bundled as CSV next to the module."""

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.datasets import longley
from statsmodels.tsa.arima.model import ARIMA


def rounded(values, digits: int = 4) -> dict[str, float]:
    return {str(name): round(float(value), digits) for name, value in values.items()}


def main() -> None:
    rng = np.random.default_rng(42)
    frame = pd.DataFrame({"x1": rng.normal(size=200), "x2": rng.normal(size=200),
                          "group": rng.choice(["a", "b"], 200)})
    frame["y"] = (1.5 + 2.0 * frame.x1 - 0.5 * frame.x2 + (frame.group == "b")
                  + rng.normal(scale=0.3, size=200))
    ols = smf.ols("y ~ x1 + x2 + C(group)", data=frame).fit()
    print("ols params", rounded(ols.params))
    print("ols rsquared", round(float(ols.rsquared), 4))

    frame["count"] = rng.poisson(np.exp(0.3 + 0.4 * frame.x1))
    poisson = smf.glm("count ~ x1", data=frame, family=sm.families.Poisson()).fit()
    print("glm poisson", rounded(poisson.params))

    noise = rng.normal(size=300)
    series = np.zeros(300)
    for i in range(1, 300):
        series[i] = 0.6 * series[i - 1] + noise[i]
    arima = ARIMA(series, order=(1, 0, 0)).fit()
    print("arima", rounded(pd.Series(arima.params, index=["const", "ar.L1", "sigma2"]), 3))

    data = longley.load_pandas()
    print("longley", data.data.shape,
          list(data.endog_name) if isinstance(data.endog_name, list) else data.endog_name)
    fit = sm.OLS(data.endog, sm.add_constant(data.exog[["GNP", "POP"]])).fit()
    print("longley ols", rounded(fit.params, 3))
