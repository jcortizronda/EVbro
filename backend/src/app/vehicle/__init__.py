"""Car channel: mutual-TLS MQTT, live state and polling policy.

Comes from `custom_components/ebro/vehicle/` in the Home Assistant integration and is kept
as close to it as possible (see AGENTS.md §13). It is pure code: it knows nothing about FastAPI
or the database; `services/car_events.py` wires it into EVBro.
"""
