"""NEWCORE slice S1 adapters: the in-process implementations of the step-0 ports.

    fake_venue      FakeVenue      VenuePort, deterministic and driven by the replay candle path (zb-path/1)

The durable file journal (NC-02a) and the testnet venue are separate adapters of the same ports (S3 / S5).
"""
from .fake_venue import CostModel, FakeVenue, path_points
