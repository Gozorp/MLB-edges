"""
data_sources
------------
Loaders for external data that isn't fetched via pybaseball/Statcast.

Each module here owns the file format, the API lookup, and the team-level
aggregation for one external source. `build_pipeline` imports the
team-level getters (e.g. `bat_tracking_gap_features`) and treats everything
below them as an opaque black box.
"""
