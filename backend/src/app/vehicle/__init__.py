"""Canal del coche: MQTT mutual-TLS, estado en vivo y política de sondeo.

Procede de `custom_components/ebro/vehicle/` de la integración de Home Assistant y se
mantiene lo más parecido posible a ella (ver AGENTS.md §13). Es código puro: no conoce FastAPI
ni la base de datos; `services/car_events.py` lo conecta con EVBro.
"""
