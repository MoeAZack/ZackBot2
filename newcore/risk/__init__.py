"""NEWCORE risk v0 (slice S4): Cairo trading day, book policy (max positions, leverage cap, daily-loss halt, drawdown
kill). Pure: fed by the Runner, records nothing itself."""
from .book import BookPolicy, BookRisk, RiskEvent
from .cairo import CAIRO, cairo_day, cairo_offset_hours
