"""NC-01 test settings. Hypothesis is pinned (requirements-dev.txt) and fully deterministic here: every property carries
an explicit @seed, the example database is off (nothing is written to the repo) and there is no wall-clock deadline."""
from hypothesis import HealthCheck, settings

settings.register_profile('nc01', database=None, deadline=None, max_examples=150, print_blob=True,
                          suppress_health_check=(HealthCheck.too_slow,))
settings.load_profile('nc01')
