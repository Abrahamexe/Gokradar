"""Gecikme modeli özellikleri. Eğitim ve canlı tahmin aynı kodu kullanır.

Özellikler (hepsi kalkıştan önce bilinir):
- Zaman: haftanın günü, planlanan kalkış saati
- Uçuş: havayolu, kalkış, varış (seyrek varışlar → "OTHER"), mesafe
- Hava (kalkış havalimanı, planlanan saate en yakın METAR): sıcaklık, rüzgâr, hamle,
  görüş, bulut tavanı, yağış
- Yoğunluk: havalimanı × haftanın günü × saat için tipik planlanan kalkış sayısı
  (yalnızca eğitim döneminden hesaplanır; canlıda da aynı değer kullanılır)
"""
from dataclasses import dataclass, field

import pandas as pd

import config

OTHER = "OTHER"
CATEGORICAL = ["airline", "origin", "dest"]
ORDINAL = ["day_of_week", "dep_hour"]
WEATHER = ["temp_c", "wind_kt", "gust_kt", "visibility_mi", "ceiling_ft", "precip"]
NUMERIC = ["distance", *WEATHER, "sched_deps_hour"]
FEATURES = [*CATEGORICAL, *ORDINAL, *NUMERIC]

# Kalkış sonrası bilgiler — özellik olarak asla kullanılmaz (veri sızıntısı)
FORBIDDEN = {
    "dep_time", "deptime", "dep_delay", "depdelay", "dep_delay_minutes", "depdelayminutes",
    "departure_delay_groups", "wheels_off", "wheelsoff", "wheels_on", "wheelson",
    "taxi_out", "taxiout", "taxi_in", "taxiin", "arr_time", "arrtime", "arr_delay",
    "arrdelay", "arr_del15", "arrdel15", "actual_elapsed_time", "air_time", "airtime",
    "carrier_delay", "carrierdelay", "weather_delay", "weatherdelay", "nas_delay", "nasdelay",
    "security_delay", "securitydelay", "late_aircraft_delay", "lateaircraftdelay",
    "cancelled", "diverted", "dep_del15",
}

CEILING_UNLIMITED_FT = 30_000.0   # tavan yoksa / çok yüksekse
VISIBILITY_MAX_MI = 10.0          # METAR "10+" üst sınırı
WEATHER_TOLERANCE = pd.Timedelta(minutes=90)


def departure_utc(flight_date: pd.Series, crs_dep_time: pd.Series, origin: pd.Series) -> pd.Series:
    """Yerel planlanan kalkış (tarih + HHMM) → UTC zaman damgası."""
    local = (pd.to_datetime(flight_date).dt.normalize()
             + pd.to_timedelta(crs_dep_time // 100, unit="h")
             + pd.to_timedelta(crs_dep_time % 100, unit="m"))
    out = pd.Series(pd.NaT, index=local.index, dtype="datetime64[ns, UTC]")
    for airport, idx in origin.groupby(origin).groups.items():
        tz = config.AIRPORT_TZ.get(airport)
        if tz is None:
            raise KeyError(f"{airport} için saat dilimi config.AIRPORT_TZ'de tanımlı değil.")
        out.loc[idx] = (local.loc[idx]
                        .dt.tz_localize(tz, ambiguous="NaT", nonexistent="shift_forward")
                        .dt.tz_convert("UTC"))
    return out


def attach_weather(flights: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    """Her uçuşa kalkış havalimanının planlanan saate en yakın METAR'ını ekler (±90 dk)."""
    df = flights.copy()
    df["dep_utc"] = departure_utc(df["flight_date"], df["crs_dep_time"], df["origin"])
    df["_row"] = range(len(df))
    left = df.sort_values("dep_utc")
    right = (weather[["station", "valid_utc", *WEATHER]]
             .rename(columns={"station": "origin"})
             .astype({"valid_utc": "datetime64[ns, UTC]"})
             .sort_values("valid_utc"))
    merged = pd.merge_asof(left, right, left_on="dep_utc", right_on="valid_utc", by="origin",
                           direction="nearest", tolerance=WEATHER_TOLERANCE)
    return merged.sort_values("_row").drop(columns=["_row", "valid_utc"]).reset_index(drop=True)


def _dep_hour(crs_dep_time: pd.Series) -> pd.Series:
    return (crs_dep_time // 100).clip(0, 23).astype("int8")


@dataclass
class DelayFeatureBuilder:
    """Eğitim verisinden sözlükleri, yoğunluk profilini ve rota mesafelerini öğrenir;
    aynı dönüşümü eğitim, test ve canlı tahmine uygular."""
    min_dest_flights: int = config.MIN_DEST_FLIGHTS
    airlines_: list[str] = field(default_factory=list)
    origins_: list[str] = field(default_factory=list)
    dests_: list[str] = field(default_factory=list)
    congestion_: dict = field(default_factory=dict)
    route_distance_: dict = field(default_factory=dict)

    def fit(self, df: pd.DataFrame) -> "DelayFeatureBuilder":
        dest_counts = df["dest"].value_counts()
        self.dests_ = sorted(dest_counts[dest_counts >= self.min_dest_flights].index) + [OTHER]
        self.airlines_ = sorted(df["airline"].unique()) + [OTHER]
        self.origins_ = sorted(df["origin"].unique())
        self.congestion_ = self._congestion_profile(df)
        self.route_distance_ = df.groupby(["origin", "dest"])["distance"].median().to_dict()
        return self

    @staticmethod
    def _congestion_profile(df: pd.DataFrame) -> dict:
        """(havalimanı, haftanın günü, saat) → o saatte ortalama planlanan kalkış sayısı."""
        dates = pd.to_datetime(df["flight_date"])
        counts = (pd.DataFrame({"origin": df["origin"], "dow": dates.dt.dayofweek,
                                "hour": _dep_hour(df["crs_dep_time"])})
                  .value_counts())
        days_per_dow = dates.drop_duplicates().dt.dayofweek.value_counts()
        n_days = counts.index.get_level_values("dow").map(days_per_dow).to_numpy()
        return (counts / n_days).to_dict()

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """Gerekli girdiler: flight_date, airline, origin, dest, crs_dep_time ve WEATHER
        kolonları. distance yoksa eğitimdeki rota mesafesinden bulunur."""
        dates = pd.to_datetime(df["flight_date"])
        X = pd.DataFrame(index=df.index)
        X["airline"] = pd.Categorical(df["airline"].where(df["airline"].isin(self.airlines_), OTHER),
                                      categories=self.airlines_)
        X["origin"] = pd.Categorical(df["origin"], categories=self.origins_)
        X["dest"] = pd.Categorical(df["dest"].where(df["dest"].isin(self.dests_), OTHER),
                                   categories=self.dests_)
        X["day_of_week"] = dates.dt.dayofweek.astype("int8")
        X["dep_hour"] = _dep_hour(df["crs_dep_time"])

        if "distance" in df:
            X["distance"] = df["distance"].astype("float32")
        else:
            X["distance"] = [self.route_distance_.get(k) for k in zip(df["origin"], df["dest"])]
            X["distance"] = X["distance"].astype("float32")

        X["temp_c"] = df["temp_c"].astype("float32")
        X["wind_kt"] = df["wind_kt"].astype("float32")
        X["gust_kt"] = df["gust_kt"].astype("float32").fillna(0.0)  # boş = hamle yok
        X["visibility_mi"] = df["visibility_mi"].astype("float32").clip(upper=VISIBILITY_MAX_MI)
        X["ceiling_ft"] = (df["ceiling_ft"].astype("float32")
                           .fillna(CEILING_UNLIMITED_FT).clip(upper=CEILING_UNLIMITED_FT))
        X["precip"] = df["precip"].astype("float32")  # METAR eşleşmezse NaN kalır

        keys = zip(df["origin"], X["day_of_week"], X["dep_hour"])
        X["sched_deps_hour"] = pd.Series([self.congestion_.get(k, 0.0) for k in keys],
                                         index=df.index, dtype="float32")

        leaked = FORBIDDEN & set(X.columns)
        assert not leaked, f"Sızıntı kolonu özelliklerde: {leaked}"
        return X[FEATURES]
