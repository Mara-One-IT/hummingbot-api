"""
Candle feeds for exchanges hummingbot's CandlesFactory does not (yet) support.

Both Bitstamp and Coinbase Advanced Trade lack a usable OHLC websocket channel
(Bitstamp has none at all; Coinbase's "candles" channel is fixed at 5m granularity),
so these feeds poll the public REST candles endpoint instead of listening to a
websocket. The CandlesBase bootstrap contract is preserved: the first successful
poll seeds the deque, sets ``_ws_candle_available`` and spawns
``fill_historical_candles()``, which paginates backwards to fill the deque to
``max_records`` exactly as the websocket implementations do.

Call ``register_candle_feeds()`` once at startup to add these to
``CandlesFactory._candles_map`` (upstream implementations, if they appear in a
future hummingbot release, take precedence).
"""
import asyncio
import logging
from typing import List, Optional

from bidict import bidict

from hummingbot.core.api_throttler.data_types import RateLimit
from hummingbot.core.network_iterator import NetworkStatus
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.data_feed.candles_feed.candles_base import CandlesBase
from hummingbot.data_feed.candles_feed.candles_factory import CandlesFactory
from hummingbot.logger import HummingbotLogger


class RestPollingCandlesBase(CandlesBase):
    """
    CandlesBase variant that maintains live candles by polling REST instead of a websocket.

    ``listen_for_subscriptions`` (the task started by ``start_network``) is replaced with a
    polling loop; the websocket hook methods are therefore never called.
    """

    _logger: Optional[HummingbotLogger] = None

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    @property
    def poll_interval(self) -> float:
        # Often enough that the in-progress candle's close tracks the market
        # (10s at 1m), without hammering the endpoint at large intervals.
        return min(max(self.interval_in_seconds / 6.0, 10.0), 60.0)

    async def listen_for_subscriptions(self):
        while True:
            try:
                # Window ends at the current in-progress interval so its live candle is included.
                end_time = self._round_timestamp_to_interval_multiple(int(self._time())) + self.interval_in_seconds
                candles = await self.fetch_candles(end_time=end_time, limit=5)
                if candles is not None and getattr(candles, "ndim", 0) == 2 and len(candles) > 0:
                    self._merge_polled_candles(candles)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().exception(
                    f"Unexpected error polling {self.name} candles via REST. Retrying in {self.poll_interval}s..."
                )
            await self._sleep(self.poll_interval)

    def _merge_polled_candles(self, candles):
        if len(self._candles) == 0:
            # Bootstrap: seed with the newest candle and let the base class's
            # fill_historical_candles() paginate backwards to max_records.
            self._candles.append(candles[-1])
            self._ws_candle_available.set()
            self._fill_candles_task = safe_ensure_future(self.fill_historical_candles())
            return
        latest = int(self._candles[-1][0])
        new_rows = [row for row in candles if int(row[0]) > latest]
        if new_rows and int(new_rows[0][0]) > latest + self.interval_in_seconds:
            # The poll window no longer overlaps the stored candles (e.g. after a long
            # network outage) - appending would leave a gap. Reset and re-bootstrap.
            self.logger().warning(f"Gap detected in {self.name} candles. Resetting feed...")
            self._reset_candles()
            return
        for row in candles:
            ts = int(row[0])
            if ts > latest:
                self._candles.append(row)
                latest = ts
            elif ts == int(self._candles[-1][0]):
                self._candles[-1] = row  # refresh the in-progress candle

    async def check_network(self) -> NetworkStatus:
        rest_assistant = await self._api_factory.get_rest_assistant()
        await rest_assistant.execute_request(url=self.health_check_url,
                                             throttler_limit_id=self._health_check_limit_id)
        return NetworkStatus.CONNECTED

    @property
    def _health_check_limit_id(self) -> str:
        raise NotImplementedError

    @property
    def wss_url(self):
        return None  # REST-polling feed: no websocket


class BitstampSpotCandles(RestPollingCandlesBase):
    """https://www.bitstamp.net/api/#tag/Market-info/operation/GetOHLCData"""

    REST_URL = "https://www.bitstamp.net"
    CANDLES_LIMIT_ID = "BitstampOHLC"
    HEALTH_CHECK_ENDPOINT = "/api/v2/trading-pairs-info/"
    MAX_RESULTS_PER_REST_REQUEST = 1000

    @property
    def name(self):
        return f"bitstamp_{self._trading_pair}"

    @property
    def rest_url(self):
        return self.REST_URL

    @property
    def health_check_url(self):
        return self.rest_url + self.HEALTH_CHECK_ENDPOINT

    @property
    def _health_check_limit_id(self):
        return self.HEALTH_CHECK_ENDPOINT

    @property
    def candles_endpoint(self):
        return self.CANDLES_LIMIT_ID  # throttler limit id (URL itself is per-pair)

    @property
    def candles_url(self):
        return f"{self.rest_url}/api/v2/ohlc/{self._ex_trading_pair}/"

    @property
    def candles_max_result_per_rest_request(self):
        return self.MAX_RESULTS_PER_REST_REQUEST

    @property
    def rate_limits(self):
        # Bitstamp allows 8000 requests per 10 minutes (~13/s); stay well below.
        return [
            RateLimit(limit_id=self.CANDLES_LIMIT_ID, limit=5, time_interval=1),
            RateLimit(limit_id=self.HEALTH_CHECK_ENDPOINT, limit=5, time_interval=1),
        ]

    @property
    def intervals(self):
        return bidict({
            "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
            "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400, "3d": 259200,
        })

    def get_exchange_trading_pair(self, trading_pair):
        return trading_pair.replace("-", "").lower()

    def _get_rest_candles_params(self,
                                 start_time: Optional[int] = None,
                                 end_time: Optional[int] = None,
                                 limit: Optional[int] = None) -> dict:
        step = self.intervals[self.interval]
        # start/end are inclusive, so the window spans (end-start)/step + 1 candles.
        n_candles = (end_time - start_time) // step + 1
        return {
            "step": step,
            "start": start_time,
            "end": end_time,
            "limit": max(1, min(self.MAX_RESULTS_PER_REST_REQUEST, n_candles)),
        }

    def _parse_rest_candles(self, data: dict, end_time: Optional[int] = None) -> List[List[float]]:
        # {"data": {"pair": "BTC/USD", "ohlc": [{"timestamp", "open", "high", "low", "close", "volume"}, ...]}}
        # sorted ascending (oldest first).
        rows = data.get("data", {}).get("ohlc", []) if isinstance(data, dict) else []
        candles = []
        for r in rows:
            timestamp = self.ensure_timestamp_in_seconds(r["timestamp"])
            volume = float(r["volume"])
            quote_asset_volume = volume * float(r["close"])
            candles.append([timestamp, r["open"], r["high"], r["low"], r["close"], volume,
                            quote_asset_volume, 0., 0., 0.])
        return candles


class CoinbaseAdvancedTradeSpotCandles(RestPollingCandlesBase):
    """https://docs.cdp.coinbase.com/advanced-trade/reference/retailbrokerageapi_getpubliccandles"""

    REST_URL = "https://api.coinbase.com"
    CANDLES_LIMIT_ID = "CoinbaseCandles"
    HEALTH_CHECK_ENDPOINT = "/api/v3/brokerage/time"
    # The API rejects any request whose start/end window spans >=350 candles,
    # regardless of the "limit" param, so keep a margin below that.
    MAX_RESULTS_PER_REST_REQUEST = 340

    @property
    def name(self):
        return f"coinbase_advanced_trade_{self._trading_pair}"

    @property
    def rest_url(self):
        return self.REST_URL

    @property
    def health_check_url(self):
        return self.rest_url + self.HEALTH_CHECK_ENDPOINT

    @property
    def _health_check_limit_id(self):
        return self.HEALTH_CHECK_ENDPOINT

    @property
    def candles_endpoint(self):
        return self.CANDLES_LIMIT_ID  # throttler limit id (URL itself is per-pair)

    @property
    def candles_url(self):
        return f"{self.rest_url}/api/v3/brokerage/market/products/{self._ex_trading_pair}/candles"

    @property
    def candles_max_result_per_rest_request(self):
        return self.MAX_RESULTS_PER_REST_REQUEST

    @property
    def rate_limits(self):
        # Coinbase public endpoints allow 10 req/s per IP.
        return [
            RateLimit(limit_id=self.CANDLES_LIMIT_ID, limit=8, time_interval=1),
            RateLimit(limit_id=self.HEALTH_CHECK_ENDPOINT, limit=8, time_interval=1),
        ]

    @property
    def intervals(self):
        return bidict({
            "1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE",
            "30m": "THIRTY_MINUTE", "1h": "ONE_HOUR", "2h": "TWO_HOUR",
            "6h": "SIX_HOUR", "1d": "ONE_DAY",
        })

    def get_exchange_trading_pair(self, trading_pair):
        return trading_pair  # product_id is already BASE-QUOTE

    def _get_rest_candles_params(self,
                                 start_time: Optional[int] = None,
                                 end_time: Optional[int] = None,
                                 limit: Optional[int] = None) -> dict:
        return {
            "start": str(start_time),
            "end": str(end_time),
            "granularity": self.intervals[self.interval],
        }

    def _parse_rest_candles(self, data: dict, end_time: Optional[int] = None) -> List[List[float]]:
        # {"candles": [{"start", "low", "high", "open", "close", "volume"}, ...]}
        # sorted descending (newest first) -> reverse to ascending.
        rows = data.get("candles", []) if isinstance(data, dict) else []
        candles = []
        for r in reversed(rows):
            timestamp = self.ensure_timestamp_in_seconds(r["start"])
            volume = float(r["volume"])
            quote_asset_volume = volume * float(r["close"])
            candles.append([timestamp, r["open"], r["high"], r["low"], r["close"], volume,
                            quote_asset_volume, 0., 0., 0.])
        return candles


def register_candle_feeds():
    """Add the feeds above to CandlesFactory (a future upstream implementation wins)."""
    CandlesFactory._candles_map.setdefault("bitstamp", BitstampSpotCandles)
    CandlesFactory._candles_map.setdefault("coinbase_advanced_trade", CoinbaseAdvancedTradeSpotCandles)
