// Shapes returned by backend/forecaster/api/app.py. Nullable where pandas NaN → null.

export interface Meta {
  app_display_name: string;
  attribution: string;
  thresholds: {
    min_new_days_for_retrain: number;
    min_global_wape_improvement: number;
    max_category_wape_degradation: number;
    overfit_ratio_warning: number;
  };
}

export interface Store {
  store_id: string;
  city: string;
  lat: number;
  lon: number;
  timezone: string;
  traffic_connected: boolean;
}

export interface Metric {
  n: number;
  mae: number | null;
  rmse: number | null;
  wape: number | null;
  bias: number | null;
}

export interface WeatherFeatures {
  weather_temperature_max: number | null;
  weather_temperature_min: number | null;
  weather_precipitation_sum: number | null;
  weather_code: number | null;
  weather_wind_speed_max?: number | null;
}

export type WasteRisk = "high" | "watch" | "low";

export interface RecItem extends Partial<WeatherFeatures> {
  store_id: string;
  product_id: string;
  name: string | null;
  category: string;
  sell_price_main: number | null;
  p50: number;
  p80: number;
  p50_if_30pct_markdown: number | null;
  expected_customer_count: number | null;
  on_hand: number;
  expiring_tomorrow: number;
  shelf_life?: number | null;
  forecast_date: string;
  as_of: string;
  model_version: string;
  order_qty: number;
  expiring_leftover: number;
  excess_ratio: number;
  markdown: number;
  markdown_lift_estimate: number | null;
  waste_risk: WasteRisk;
  surplus_action?: "sell" | "markdown" | "markdown_then_donate" | "donate";
  donate_units?: number;
}

export interface Recommendations {
  store_id: string;
  as_of: string | null;
  forecast_date: string | null;
  model_version: string | null;
  critical_ratio: number;
  waste_cost_ratio: number;
  weather: WeatherFeatures;
  expected_customers: number | null;
  items: RecItem[];
  note: string;
  inventory_basis?: string;
  donations?: { units: number; kg_assumed: number; co2e_kg: number; meals: number };
}

export interface LiveWeatherDay extends WeatherFeatures {
  date: string;
  precipitation_probability_max: number | null;
  weather_source: string;
}

export interface LiveWeather {
  store_id: string;
  city: string;
  fetched_at: string;
  days: LiveWeatherDay[];
}

export interface ModelHealth {
  context: string;
  champion: string;
  training_range: [string, string];
  trained_on: string;
  feature_schema_version: string;
  metrics: {
    train?: Metric;
    test?: Metric;
    eval?: Metric;
    p80_coverage?: number;
  } | null;
  decision_reason: string | null;
  new_first_party_days: number;
  retrain_threshold_days: number;
  retrain_eligible: boolean;
  principle: string;
}

export interface ModelVersion {
  version: string;
  stage: string;
  training_start: string | null;
  training_end: string | null;
  feature_schema_version: string;
  params: Record<string, unknown> | null;
  metrics: { train?: Metric; test?: Metric; eval?: Metric; p80_coverage?: number } | null;
  artifact_uri: string;
  created_at: string;
  promotion_status: "candidate" | "champion" | "rejected" | "retired" | string;
  decision_reason: string | null;
  context: string;
}

export interface EvalBlock {
  global: Metric;
  by_category: Record<string, Metric>;
}

export interface RetrainChecks {
  global_improvement: number;
  global_improvement_ok: boolean;
  category_regressions: Record<string, number>;
  category_ok: boolean;
  beats_baselines: boolean;
  overfit_ratio: number | null;
  overfit_ok: boolean;
}

export interface RetrainRun {
  run_id: string;
  context: string;
  as_of_date: string;
  trigger: string;
  champion_version: string;
  candidate_version: string | null;
  new_observation_days: number;
  comparison: {
    champion: EvalBlock;
    challenger: EvalBlock;
    baselines: Record<string, EvalBlock>;
    checks: RetrainChecks;
    eval_window: [string, string];
  } | null;
  decision: "skipped" | "rejected" | "promoted" | string;
  reason: string;
  created_at: string;
}

export interface TimeseriesDay {
  forecast_date: string;
  abs_err: number;
  err: number;
  actual: number;
  version: string;
  wape: number | null;
  bias: number | null;
  wape_7d: number | null;
  wape_30d: number | null;
  drift: boolean;
}

export interface Timeseries {
  days: TimeseriesDay[];
  reference_wape: number | null;
  drift_multiplier?: number;
}

export interface SegmentMetric extends Metric {
  segment: string;
}

export interface LedgerRow {
  prediction_id: string;
  context: string;
  store_id: string;
  product_id: string;
  category: string | null;
  prediction_created_at: string;
  forecast_date: string;
  horizon: number;
  predicted_units: number;
  p80_units: number | null;
  weather_forecast: (Partial<WeatherFeatures> & { weather_source?: string | null }) | null;
  expected_customers: number | null;
  model_version: string;
  feature_schema_version: string;
  actual_units_sold: number | null;
  actual_customer_count: number | null;
  waste_units: number | null;
  observed_at: string | null;
  error: number | null;
}

export interface DataSource {
  key: string;
  label: string;
  connected: boolean;
  detail: string;
  first_party_rows?: number;
}

export interface TrafficUploadResult {
  accepted: number;
  quarantined: number;
  reasons: Record<string, number>;
}

type Seg<K extends string> = Metric & { [k in K]: string };

export interface ProductionReport {
  version: string;
  as_of: string;
  forecast_date: string;
  holdout: [string, string];
  train_metrics: Metric;
  test: {
    pred: Metric;
    pred_no_traffic?: Metric;
    pred_no_weather?: Metric;
    seasonal_naive_7?: Metric;
    rolling_mean_28?: Metric;
    by_category?: Seg<"category">[];
    by_store?: Seg<"store_id">[];
    by_weekday?: Seg<"weekday">[];
  };
  p80_coverage: number | null;
  simulation?: {
    label: string;
    shelf_life_days: Record<string, number>;
    waste_cost_ratio: number;
    totals: Record<string, number>;
    by_category: Array<Record<string, number | string>>;
  };
  weather_cross_check?: Record<
    string,
    {
      days_compared: number;
      precipitation_corr?: number | null;
      precipitation_mean_abs_diff_mm?: number | null;
      wet_day_agreement?: number | null;
    }
  >;
  feature_importance?: { feature: string; share: number }[];
}

export interface PromotionExperiment {
  experiment_id: string;
  store_id: string;
  product_id: string;
  discount: number;
  start_time: string;
  end_time: string;
  inventory_before: number | null;
  forecast_without_promotion: number | null;
  actual_sales: number | null;
  inventory_after: number | null;
  waste_after: number | null;
  revenue: number | null;
  observed_lift: number | null;
}

export interface AnomalyDay {
  store_id: string;
  date: string;
  is_anomaly: boolean;
  reason: string | null;
  training_policy: "exclude" | "feature" | "keep" | string;
}
