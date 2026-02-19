from enum import Enum


class AssetClass(str, Enum):
    EQUITY = "equity"
    CRYPTO = "crypto"
    BOND = "bond"
    COMMODITY = "commodity"
    FUTURES = "futures"
    OPTION = "option"


class BranchType(str, Enum):
    EQUITIES = "equities"
    CRYPTO = "crypto"
    BONDS = "bonds"
    COMMODITIES = "commodities"
    QUANT = "quant"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"
    SHORT = "short"
    COVER = "cover"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class ExecutionMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class RiskAlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SignalDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
