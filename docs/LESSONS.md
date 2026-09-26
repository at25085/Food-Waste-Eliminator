# Struggles and lessons (HackGT 13 build log)

What went wrong, how we found it, what we changed, and the measured result. Numbers come from the
generated files in `artifacts/` unless noted.

## Data

| Struggle | What we learned / did |
|---|---|
| The Kaggle Rohlik data needs a logged-in account and accepted competition rules. | Found the same data as Parquet in the fev-bench mirror on HuggingFace (`autogluon/fev_datasets`) — no credentials, reproducible download. |
| Two "customer" series disagreed: the sales file's `total_orders` vs the orders file's `orders` (correlation 0.95, not identical). | Used the sales file's series (covers every day we forecast) as the traffic feed; kept the other as a cross-check and documented the difference. |
| No public grocery dataset records waste or expiry. | Built a transparent first-in-first-out shelf-life simulator and labeled every waste number "simulated". Real waste only comes from owner uploads. |
| Rohlik records sales in pieces *or* kg depending on the product. | Weight-based impact (kg, CO2e, meals) needs assumed unit weights per category — shown as assumptions next to the numbers. |
| The sample store is built from a warehouse the model already knows. | Its dramatic retrain gain (25.3% → 11.3% error) is partly familiarity; we say so wherever it's shown. |

## Weather

| Struggle | What we learned / did |
|---|---|
| Training on observed weather but serving on forecasts would make offline accuracy look better than real life. | Trained on archived forecasts; for the 2024 replay used the forecast actually issued the day before (Open-Meteo Previous Runs). |
| Previous Runs data starts 2024-01-20, not Jan 1, and has no daily variables. | Replay window starts 2024-01-20; daily values are aggregated from hourly `_previous_day1` data. |
| Weather didn't help — measured three times (16.17% vs 16.06%; 15.24% vs 15.09%; 14.20% vs 14.14% without). | For an online grocer, rain doesn't stop anyone ordering. We report it rather than claim a benefit; the pipeline keeps weather for stores where it might matter. |

## Training and evaluation

| Struggle | What we learned / did |
|---|---|
| **First replay: every challenger was rejected**, even with more data. | Early stopping held out the newest 7 weeks, so the new data was only ever used for validation, never learned from. Fix: pick the tree count on that window, then refit on the whole snapshot. Result: 6 of 10 challengers promoted, error falling from ~16% to ~13% on recent windows. |
| **Easter:** error spiked on the days *before* the holiday (Mar 27–28: 24–25% error, −20% under-forecast). | A 0/1 "is it a holiday" flag can't say "Easter is in two days", and serving set tomorrow's holiday flag to 0 even though holidays are known in advance. Added holiday-proximity features from the public calendar: pre-Easter error 22.0% → 18.3%. |
| Most "obvious" accuracy ideas didn't work. | Measured on a fixed harness, kept only if ≥0.3 points better with no category >5% worse: seven discount types **kept** (15.70% → 15.24%); dropping stockout days as targets, a produce specialist, per-store calibration and spike weighting all **rejected**. |
| A "simplify to one model" idea looked obviously good. | One model giving both the median and the 80th percentile was 2 points worse (bakery +24%) — rejected. Dropping the separate traffic-forecast model was simpler *and* better (15.24% → 15.10%) — adopted. |
| A single lucky fortnight could promote a model. | Promotion now also requires the challenger not to lose in either week of its evaluation window. |
| A model trained on one feature list could be served on another. | Every model records its feature-schema version; serving and retraining refuse a mismatch. |

## Decisions (orders, markdowns, waste)

| Struggle | What we learned / did |
|---|---|
| The waste simulator ordered a full day's amount on top of stock already on the shelf. | Waste was inflated. Orders are now net of stock that is still sellable tomorrow, with the same rule for our model and the baseline, so the comparison measures forecast skill only. |
| **Our waste numbers were flattering.** The default cost setting ran the simulated store at ~13% stockouts, so waste looked tiny. | Moved the default to a realistic ~5% lost-sales service level and report waste *and* lost sales together: −87% waste and −22% lost sales vs "same as last week". |
| The demo plan showed no waste risk at all. | Starting stock came from our own conservative policy, so shelves were nearly empty. Install-day stock now comes from the store's legacy habit (last week + buffer), labeled. |
| "Smallest discount that clears the surplus" was wrong — a teammate's insight. | A discount applies to every unit sold, including ones that would have sold at full price. Now every discount level and start day is simulated with the trained model's predicted sales, and the option that keeps the most money wins — sometimes "no discount pays for itself, donate". |
| The model's estimated lift was sometimes zero or negative at a deeper discount. | Lift is made monotone (a deeper discount never sells less) before planning. |
| "Unsold today = spoiled" would be wrong: deliveries arrive on different days with different shelf lives. | Stock is tracked per delivery (cohorts): owner batch sheet when available, otherwise first-in-first-out from the daily sheet with per-product shelf lives. |

## Learning from real stores

| Struggle | What we learned / did |
|---|---|
| New stores start badly (cold start: 21–25% error). | Owner uploads are graded against the forecasts made for them, then retrain the model. |
| Store-specific models vs one model. | Switched to **one global model**: a store's uploads retrain it, and it is promoted only if it is better for that store **and** no worse for the existing stores. |
| A later retrain would have dropped earlier stores' uploads. | Every retrain now includes every store's uploaded sheets. |
| "Learned from this store" showed yes before any retraining. | It compared dates; it now reads the model's actual training record. |
| Stockout days make sales look lower than demand. | Detected from the sheets (shelf ended empty after selling everything on hand) and fed to the model's in-stock inputs. |

## Engineering

| Struggle | What we learned / did |
|---|---|
| Windows console crashed the pipeline on a "≥" in a log line. | Force UTF-8 output in every CLI. |
| Removing a feature crashed the replay at its first ledger write. | Grep for every reference before removing a column; the fix took one line, the rerun 45 minutes. |
| An interrupted command left partial uploads in the real database. | Cleaned them up and moved all experiments to isolated database copies. |
| Docker image was 3.3 GB. | XGBoost's Linux wheel pulled CUDA libraries a CPU server never uses → `xgboost-cpu`; the image dropped to 1.1 GB. Then learned uploads need the model files on the server → models and training data go on a persistent volume. |
| TimescaleDB refused to turn tables with ID keys into hypertables. | Hypertables need the time column in every unique key: observations use natural keys (store, product, date); graded forecasts are copied into a dedicated `forecast_errors` hypertable with a continuous aggregate. |
| Re-copying data into Postgres failed on foreign keys. | Clear child tables before parents, then copy parents before children. |

## Research and strategy

| Struggle | What we learned / did |
|---|---|
| The name "WasteLess" belongs to a funded company selling AI markdowns to grocers. | Rename before submission. |
| Most ideas we researched already existed — six of eleven had been shipped or built at hackathons within months. | Novelty comes from the combination and the rigor (a visible, governed learning loop on real data), not the category. |
| Confused sponsor challenges with main tracks. | One main track (A Marina's Mission); Meta/Visa/etc. are separate challenges that stack on top. |
| Web-search budget ran out mid-research. | Fell back to direct page fetches, Devpost/GitHub/arXiv/HN — and said so in the report. |

## What we'd tell the next team
1. Measure everything against a simple baseline; most clever ideas don't beat it.
2. Promotion rules matter more than the model: a new model must *earn* its place on data it hasn't seen.
3. Watch for flattering numbers — check the other side of every trade-off (waste vs lost sales).
4. Label what's simulated, every time; it's the first thing a good judge asks.
5. Test risky operations on a copy of the data.
