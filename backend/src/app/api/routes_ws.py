"""WebSocket channel for real-time telemetry subscription."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.db.session import async_session_factory
from app.security.access import accessible_vehicles
from app.security.auth import websocket_user
from app.services.poller import VehiclePoller

_LOGGER = logging.getLogger(__name__)
router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws")
async def websocket_telemetry_stream(websocket: WebSocket):
    """Real-time two-way channel for the web dashboard."""
    # without a valid session (or from another origin) the connection is rejected before being accepted.
    user = await websocket_user(websocket)
    if user is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    poller = VehiclePoller.get_instance()
    queue = asyncio.Queue()

    # Each connection only receives the cars its account can see. The set is refreshed every ALLOWED_REFRESH_S whether or not
    # events keep flowing: when a manager revokes an access, the revoked connection must go quiet within that time.
    allowed: set = set()
    ALLOWED_REFRESH_S = 60

    async def _refresh_allowed() -> None:
        async with async_session_factory() as db:
            allowed.clear()
            allowed.update((await accessible_vehicles(db, user)).keys())

    await _refresh_allowed()

    def _on_event(event_dict):
        if event_dict.get("vin") not in allowed:          # an event without a car, or for somebody else's car, is dropped
            return
        try:
            queue.put_nowait(event_dict)
        except Exception:
            pass

    poller.register_ws_subscriber(_on_event)
    _LOGGER.info("Client connected to the telemetry WebSocket.")

    try:
        # send the current state of every vehicle right away
        for vin in [v for v in list(poller._contexts) if v in allowed]:
            latest = poller.get_latest(vin)
            if latest:
                await websocket.send_text(json.dumps({
                    "type": "telemetry",
                    "vin": vin,
                    "data": latest,
                }, default=str))

        # task that sends queued events to the client
        async def send_worker():
            loop = asyncio.get_running_loop()
            next_refresh = loop.time() + ALLOWED_REFRESH_S
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=max(0.1, next_refresh - loop.time()))
                except asyncio.TimeoutError:
                    msg = None
                if loop.time() >= next_refresh:
                    await _refresh_allowed()
                    next_refresh = loop.time() + ALLOWED_REFRESH_S
                if msg is not None and msg.get("vin") in allowed:   # checked again: the access may have gone while it waited in the queue
                    await websocket.send_text(json.dumps(msg, default=str))

        send_task = asyncio.create_task(send_worker())

        # receive loop (pings / commands, if any)
        while True:
            text = await websocket.receive_text()
            # room for handling commands or keep-alives from the front end
            if text == "ping":
                await websocket.send_text("pong")

    except WebSocketDisconnect:
        _LOGGER.info("Cliente WebSocket desconectado.")
    finally:
        poller.unregister_ws_subscriber(_on_event)
        if 'send_task' in locals():
            send_task.cancel()
