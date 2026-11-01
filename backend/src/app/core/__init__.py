"""EVBro's Ebro/Chery protocol core.

Holds the logic verified in the field: BFF authentication, request signing, the command
catalogue and sending, the realtime probe, waking the car. It comes from the Home Assistant
integration for these cars and is kept as close to it as possible so fixes can be synced
both ways. Neither the API nor the interface ever talks to the cloud directly: everything
goes through here.

The modules in this folder import each other with package-relative imports
(`from .core import …` / `from . import …`), so Python guarantees which module gets
loaded and the loggers are attributed to EVBro.
"""
