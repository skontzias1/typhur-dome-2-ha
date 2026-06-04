"""Typhur AWS IoT MQTT client with TLS client-cert auth and auto-reconnect."""

from __future__ import annotations

import logging
import os
import ssl
import tempfile
import time
from collections.abc import Callable
from typing import Any

from .api import MqttCredentials

_LOGGER = logging.getLogger(__name__)

MessageCallback = Callable[[str, dict[str, Any]], None]


class TyphurMqttClient:
    """
    Wraps paho-mqtt and bridges it to the HA asyncio event loop.

    paho-mqtt is synchronous.  We call ``loop_start()`` which spawns a
    background thread for I/O, and use ``hass.loop.call_soon_threadsafe``
    to dispatch incoming messages back onto the HA event loop.
    """

    def __init__(
        self,
        hass_loop,                    # asyncio event loop from hass.loop
        creds: MqttCredentials,
        topics: list[str],
        on_message: MessageCallback,
        on_connected: Callable[[], None] | None = None,
        on_disconnected: Callable[[], None] | None = None,
    ) -> None:
        self._loop = hass_loop
        self._creds = creds
        self._topics = topics
        self._on_message = on_message
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected

        self._client = None
        self._connected = False
        self._tmpdir: tempfile.TemporaryDirectory | None = None

    # ── Public API ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Connect and start the background I/O loop."""
        self._tmpdir = tempfile.TemporaryDirectory()
        self._client = self._build_client()
        self._client.connect_async(self._creds.endpoint, self._creds.port, keepalive=60)
        self._client.loop_start()
        _LOGGER.debug(
            "Typhur MQTT: connecting to %s:%s", self._creds.endpoint, self._creds.port
        )

    def stop(self) -> None:
        """Disconnect and stop the background thread."""
        if self._client:
            # Detach callbacks BEFORE tearing down. Otherwise the disconnect()
            # we are about to call fires on_disconnect → on_disconnected, which
            # schedules another reconnect that calls stop() again … an infinite
            # "Normal disconnection" storm. A stop is intentional: it must not
            # look like a connection we should recover.
            self._client.on_connect = None
            self._client.on_disconnect = None
            self._client.on_message = None
            self._client.loop_stop()
            try:
                self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._client = None
        if self._tmpdir:
            self._tmpdir.cleanup()
            self._tmpdir = None
        self._connected = False

    def update_credentials(self, creds: MqttCredentials, topics: list[str]) -> None:
        """Replace credentials and reconnect (used on token/cert refresh)."""
        self._creds = creds
        self._topics = topics
        self.stop()
        self.start()

    @property
    def connected(self) -> bool:
        return self._connected

    # ── paho client setup ─────────────────────────────────────────────────

    def _build_client(self):
        import paho.mqtt.client as mqtt

        try:
            # paho-mqtt ≥ 2.0 — use v2 callback API
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=self._creds.client_id,
                protocol=mqtt.MQTTv311,
            )
            client.on_connect    = self._on_connect_v2
            client.on_message    = self._on_message_cb
            client.on_disconnect = self._on_disconnect_v2
        except AttributeError:
            # paho-mqtt 1.x fallback
            client = mqtt.Client(
                client_id=self._creds.client_id,
                protocol=mqtt.MQTTv311,
            )
            client.on_connect    = self._on_connect_v1
            client.on_message    = self._on_message_cb
            client.on_disconnect = self._on_disconnect_v1

        self._configure_tls(client)
        return client

    def _configure_tls(self, client) -> None:
        """Write cert/key to temp files and configure mTLS on the client."""
        assert self._tmpdir is not None
        tmpdir = self._tmpdir.name

        def _write(name: str, content: str) -> str | None:
            if not content:
                return None
            path = os.path.join(tmpdir, name)
            with open(path, "w") as f:
                f.write(content)
            return path

        ca_path   = _write("ca.pem",     self._creds.ca_pem)
        cert_path = _write("client.pem", self._creds.cert_pem)
        key_path  = _write("client.key", self._creds.key_pem)

        if cert_path and key_path:
            client.tls_set(
                ca_certs   = ca_path,
                certfile   = cert_path,
                keyfile    = key_path,
                cert_reqs  = ssl.CERT_REQUIRED if ca_path else ssl.CERT_NONE,
                tls_version= ssl.PROTOCOL_TLS_CLIENT if ca_path else ssl.PROTOCOL_TLS,
            )
        else:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            client.tls_set_context(context)

    # ── paho callbacks (v2 API) ───────────────────────────────────────────

    def _on_connect_v2(self, client, userdata, connect_flags, reason_code, properties):
        if reason_code.is_failure:
            _LOGGER.warning("Typhur MQTT connect failed: %s", reason_code)
            return
        self._handle_connected(client)

    def _on_disconnect_v2(self, client, userdata, disconnect_flags, reason_code, properties):
        self._handle_disconnected(reason_code)

    # ── paho callbacks (v1 API fallback) ─────────────────────────────────

    def _on_connect_v1(self, client, userdata, flags, rc):
        if rc != 0:
            _LOGGER.warning("Typhur MQTT connect failed rc=%s", rc)
            return
        self._handle_connected(client)

    def _on_disconnect_v1(self, client, userdata, rc):
        self._handle_disconnected(rc)

    # ── Shared logic ──────────────────────────────────────────────────────

    def _handle_connected(self, client) -> None:
        self._connected = True
        _LOGGER.info(
            "Typhur MQTT connected to %s:%s", self._creds.endpoint, self._creds.port
        )
        for topic in self._topics:
            client.subscribe(topic, qos=1)
            _LOGGER.debug("Typhur MQTT subscribed → %s", topic)
        if self._on_connected:
            self._loop.call_soon_threadsafe(self._on_connected)

    def _handle_disconnected(self, reason) -> None:
        # Only notify (and warn) on a real connected → disconnected transition.
        # paho's loop_start auto-reconnect can fire on_disconnect repeatedly
        # while the link is already down; without this guard each retry would
        # re-trigger the coordinator's reconnect and flood the log.
        was_connected = self._connected
        self._connected = False
        if not was_connected:
            _LOGGER.debug("Typhur MQTT still disconnected: %s", reason)
            return
        _LOGGER.warning("Typhur MQTT disconnected: %s", reason)
        if self._on_disconnected:
            self._loop.call_soon_threadsafe(self._on_disconnected)

    def _on_message_cb(self, client, userdata, msg) -> None:
        """Called in paho's thread — dispatch to HA event loop."""
        import json

        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            _LOGGER.debug("Typhur MQTT: non-JSON message on %s", msg.topic)
            return

        self._loop.call_soon_threadsafe(
            self._on_message, msg.topic, payload
        )
