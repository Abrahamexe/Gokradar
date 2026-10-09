"""Gecikme modeli özellikleri. Eğitim ve canlı tahmin aynı kodu kullanır.

Özellikler (hepsi kalkıştan önce bilinir):
- Zaman: haftanın günü, planlanan kalkış saati, ay (mevsim), en yakın ABD resmî tatiline
  gün uzaklığı (Şükran Günü, Noel, 4 Temmuz civarı yoğunluk)
- Uçuş: havayolu, kalkış, varış (seyrek varışlar → "OTHER"), mesafe
- Hava (kalkış havalimanı, planlanan saate en yakın METAR): sıcaklık, rüzgâr, hamle,
  görüş, bulut tavanı, yağış
- Varış havası (varış havalimanının aynı andaki METAR'ı): rüzgâr, hamle, görüş, tavan, yağış.
  Varıştaki kötü hava, kalkıştan önce uygulanan yer bekletmeleriyle kalkışı geciktirir.
- Yoğunluk: havalimanı × haftanın günü × saat için tipik planlanan kalkış sayısı
  (yalnızca eğitim döneminden hesaplanır; canlıda da aynı değer kullanılır)
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config

OTHER = "OTHER"
CATEGORICAL = ["airline", "origin", "dest"]
ORDINAL = ["day_of_week", "dep_hour", "month"]
WEATHER = ["temp_c", "wind_kt", "gust_kt", "visibility_mi", "ceiling_ft", "precip"]
# Varış METAR'ından (sıcaklık hariç); girdide "dest_" önekiyle gelir
DEST_WEATHER_BASE = ["wind_kt", "gust_kt", "visibility_mi", "ceiling_ft", "precip"]
DEST_WEATHER = [f"dest_{c}" for c in DEST_WEATHER_BASE]
NUMERIC = ["distance", "days_to_holiday", *WEATHER, *DEST_WEATHER, "sched_deps_hour"]
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

HOLIDAY_WINDOW_DAYS = 10         # daha uzak tatiller aynı sayılır
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
                        # Saat geri alınırken iki kez yaşanan 01:xx → standart saat (False)
                        .dt.tz_localize(tz, ambiguous=np.zeros(len(idx), dtype=bool),
                                        nonexistent="shift_forward")
                        .dt.tz_convert("UTC"))
    return out


def _nearest_metar(df: pd.DataFrame, weather: pd.DataFrame, key: str, cols: list[str],
                   prefix: str = "") -> pd.DataFrame:
    """df'ye key havalimanının dep_utc'ye en yakın METAR kolonlarını ekler (±90 dk)."""
    df = df.assign(_row=range(len(df)), _key=df[key].astype(str))
    right = (weather[["station", "valid_utc", *cols]]
             .rename(columns={"station": "_key", **{c: prefix + c for c in cols}})
             .astype({"valid_utc": "datetime64[ns, UTC]", "_key": str})
             .sort_values("valid_utc"))
    merged = pd.merge_asof(df.sort_values("dep_utc"), right, left_on="dep_utc", right_on="valid_utc",
                           by="_key", direction="nearest", tolerance=WEATHER_TOLERANCE)
    return merged.sort_values("_row").drop(columns=["_row", "_key", "valid_utc"]).reset_index(drop=True)


def attach_weather(flights: pd.DataFrame, weather: pd.DataFrame,
                   dest_weather: pd.DataFrame | None = None) -> pd.DataFrame:
    """Her uçuşa kalkış havalimanının planlanan saate en yakın METAR'ını ekler (±90 dk).
    dest_weather verilirse varış havalimanının aynı andaki METAR'ı "dest_" önekiyle eklenir."""
    df = flights.copy()
    df["dep_utc"] = departure_utc(df["flight_date"], df["crs_dep_time"], df["origin"])
    df = _nearest_metar(df, weather, "origin", WEATHER)
    if dest_weather is not None:
        df = _nearest_metar(df, dest_weather, "dest", DEST_WEATHER_BASE, prefix="dest_")
    return df


def days_to_holiday(dates: pd.Series) -> pd.Series:
    """En yakın ABD federal tatiline (önce ya da sonra) gün sayısı, en çok HOLIDAY_WINDOW_DAYS."""
    from pandas.tseries.holiday import USFederalHolidayCalendar

    days = pd.to_datetime(dates).dt.normalize()
    hol = USFederalHolidayCalendar().holidays(days.min() - pd.Timedelta(days=40),
                                              days.max() + pd.Timedelta(days=40)).to_numpy()
    if len(hol) == 0:
        return pd.Series(float(HOLIDAY_WINDOW_DAYS), index=dates.index, dtype="float32")
    d = days.to_numpy()
    pos = np.searchsorted(hol, d).clip(1, len(hol) - 1)
    nearest = np.minimum(np.abs(d - hol[pos - 1]), np.abs(hol[pos] - d)) / np.timedelta64(1, "D")
    return pd.Series(np.minimum(nearest, HOLIDAY_WINDOW_DAYS), index=dates.index, dtype="float32")


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
        X["month"] = dates.dt.month.astype("int8")

        if "distance" in df:
            X["distance"] = df["distance"].astype("float32")
        else:
            X["distance"] = [self.route_distance_.get(k) for k in zip(df["origin"], df["dest"])]
            X["distance"] = X["distance"].astype("float32")
        X["days_to_holiday"] = days_to_holiday(dates)

        X["temp_c"] = df["temp_c"].astype("float32")
        X["wind_kt"] = df["wind_kt"].astype("float32")
        X["gust_kt"] = df["gust_kt"].astype("float32").fillna(0.0)  # boş = hamle yok
        X["visibility_mi"] = df["visibility_mi"].astype("float32").clip(upper=VISIBILITY_MAX_MI)
        X["ceiling_ft"] = (df["ceiling_ft"].astype("float32")
                           .fillna(CEILING_UNLIMITED_FT).clip(upper=CEILING_UNLIMITED_FT))
        X["precip"] = df["precip"].astype("float32")  # METAR eşleşmezse NaN kalır

        # Varış METAR'ı: yoksa (eşleşmedi / canlıda alınamadı) NaN, LightGBM eksik değeri kendisi yönetir
        dest = {c: (df[c] if c in df else pd.Series(float("nan"), index=df.index)).astype("float32")
                for c in DEST_WEATHER}
        X["dest_wind_kt"] = dest["dest_wind_kt"]
        X["dest_gust_kt"] = dest["dest_gust_kt"].where(dest["dest_wind_kt"].isna(),
                                                       dest["dest_gust_kt"].fillna(0.0))
        X["dest_visibility_mi"] = dest["dest_visibility_mi"].clip(upper=VISIBILITY_MAX_MI)
        X["dest_ceiling_ft"] = (dest["dest_ceiling_ft"]
                                .where(dest["dest_wind_kt"].isna(),
                                       dest["dest_ceiling_ft"].fillna(CEILING_UNLIMITED_FT))
                                .clip(upper=CEILING_UNLIMITED_FT))
        X["dest_precip"] = dest["dest_precip"]

        keys = zip(df["origin"], X["day_of_week"], X["dep_hour"])
        X["sched_deps_hour"] = pd.Series([self.congestion_.get(k, 0.0) for k in keys],
                                         index=df.index, dtype="float32")

        leaked = FORBIDDEN & set(X.columns)
        assert not leaked, f"Sızıntı kolonu özelliklerde: {leaked}"
        return X[FEATURES]
