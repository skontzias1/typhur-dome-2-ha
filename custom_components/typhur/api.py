"""Typhur cloud REST API client."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .const import (
    API_CODE_OK,
    API_CODE_TOKEN_EXPIRED,
    APP_ID,
    APP_VERSION,
    SIGN_CONSTANT,
    TYPHUR_API_BY_REGION,
    X_REGION_BY_REGION,
)

_LOGGER = logging.getLogger(__name__)

# Stable device identifier for this integration's requests
_APP_DEVICE_SN = hashlib.md5(b"ha_typhur_integration_v1").hexdigest()


@dataclass
class TyphurDevice:
    device_id: str
    device_sn: str
    device_type: str
    device_model: str
    device_name: str
    sub_topics: list[str] = field(default_factory=list)
    last_state: dict[str, Any] = field(default_factory=dict)


@dataclass
class MqttCredentials:
    endpoint: str
    port: int
    client_id: str
    cert_pem: str
    key_pem: str
    ca_pem: str


class TyphurAuthError(Exception):
    """Raised when authentication fails."""


class TyphurApiError(Exception):
    """Raised for non-auth API errors."""


class TyphurApiClient:
    """Async REST client for the Typhur cloud API."""

    def __init__(self, email: str, password: str, region: str) -> None:
        self._email = email
        self._password = password
        self._region = region
        self._api_base = TYPHUR_API_BY_REGION[region]
        self._x_region = X_REGION_BY_REGION[region]
        self._token: str | None = None
        self._session: aiohttp.ClientSession | None = None

    # ── Session management ────────────────────────────────────────────────

    async def async_init(self) -> None:
        self._session = aiohttp.ClientSession()

    async def async_close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ── Request signing ───────────────────────────────────────────────────

    def _sign_headers(self, token: str, body_str: str = "{}") -> dict[str, str]:
        """Build the signed header dict required by all Typhur API calls."""
        nonce = uuid.uuid4().hex
        timestamp = str(int(time.time() * 1000))

        pairs = [
            ("x-appId",      APP_ID),
            ("x-appVersion", APP_VERSION),
            ("x-deviceSn",   _APP_DEVICE_SN),
            ("x-lang",       "en_US"),
            ("x-nonce",      nonce),
            ("x-region",     self._x_region),
            ("x-timestamp",  timestamp),
            ("x-token",      token),
        ]

        parts = ";".join(f"{k}={v}" for k, v in pairs)
        sign_str = f"{SIGN_CONSTANT}|{parts}|{body_str}"
        sign = hashlib.md5(sign_str.encode()).hexdigest()

        headers = dict(pairs)
        headers["x-sign"] = sign
        headers["Content-Type"] = "application/json"
        return headers

    # ── Auth ──────────────────────────────────────────────────────────────

    async def async_login(self) -> str:
        """Login and return a fresh token. Raises TyphurAuthError on failure."""
        md5_pw = hashlib.md5(self._password.encode()).hexdigest()
        body = json.dumps(
            {"accountName": self._email, "accountPassword": md5_pw, "deviceInfo": "HomeAssistant"},
            separators=(",", ":"),
        )
        data = await self._request("POST", "/app/account/login", body=body, token="none")
        self._token = data["token"]
        _LOGGER.debug("Typhur login OK for %s", self._email)
        return self._token

    async def async_ensure_token(self) -> str:
        """Return the cached token, logging in if we don't have one."""
        if not self._token:
            await self.async_login()
        return self._token  # type: ignore[return-value]

    # ── Device list ───────────────────────────────────────────────────────

    async def async_get_devices(self) -> list[TyphurDevice]:
        """Return all devices bound to this account."""
        token = await self.async_ensure_token()
        data = await self._request("POST", "/app/device/bind/list", token=token)

        devices: list[TyphurDevice] = []
        for raw in data if isinstance(data, list) else []:
            last_cmd = raw.get("lastStatusCmd", {})
            devices.append(
                TyphurDevice(
                    device_id=str(raw.get("deviceId", "")),
                    device_sn=raw.get("deviceSn", ""),
                    device_type=raw.get("deviceType", ""),
                    device_model=raw.get("deviceModel", ""),
                    device_name=raw.get("deviceName", ""),
                    sub_topics=raw.get("subTopics", []),
                    last_state=last_cmd.get("cmdData", {}),
                )
            )
        return devices

    # ── MQTT credentials ──────────────────────────────────────────────────

    async def async_get_mqtt_params(self) -> tuple[str, int]:
        """Return (endpoint, port) for the Typhur MQTT broker."""
        token = await self.async_ensure_token()
        entries = await self._request("POST", "/app/dict/list", token=token)
        for entry in entries if isinstance(entries, list) else []:
            if entry.get("dictKey") == "mqtt_conn_param":
                params = entry["dictValue"]
                return params["endpoint"], int(params.get("port", 8883))
        raise TyphurApiError("mqtt_conn_param not found in /app/dict/list response")

    async def async_get_mqtt_certs(self) -> MqttCredentials:
        """
        Fetch MQTT client credentials.

        The US API returns a PKCS#12 bundle via a CDN URL; the EU API may
        return raw PEM strings.  Both paths are handled here.
        """
        token = await self.async_ensure_token()
        endpoint, port = await self.async_get_mqtt_params()
        cert_data = await self._request("POST", "/app/mqtt/cert/apply", token=token)

        client_id = cert_data.get("clientId", f"ha-typhur-{uuid.uuid4().hex[:8]}")

        if "p12Url" in cert_data:
            cert_pem, key_pem, ca_pem = await self._extract_p12(
                cert_data["p12Url"],
                cert_data.get("p12Password", "").encode(),
            )
        else:
            ca_pem   = cert_data.get("ca")   or cert_data.get("caCert")    or ""
            cert_pem = cert_data.get("cert") or cert_data.get("clientCert") or ""
            key_pem  = cert_data.get("key")  or cert_data.get("clientKey")  or ""

        return MqttCredentials(
            endpoint=endpoint,
            port=port,
            client_id=client_id,
            cert_pem=cert_pem,
            key_pem=key_pem,
            ca_pem=ca_pem,
        )

    async def _extract_p12(
        self, url: str, password: bytes
    ) -> tuple[str, str, str]:
        """Download a PKCS#12 bundle and extract (cert_pem, key_pem, ca_pem)."""
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
            pkcs12,
        )

        assert self._session is not None
        async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            p12_bytes = await resp.read()

        private_key, certificate, extra_certs = pkcs12.load_key_and_certificates(
            p12_bytes, password
        )

        cert_pem = certificate.public_bytes(Encoding.PEM).decode()
        key_pem = private_key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        ).decode()
        ca_pem = (
            "".join(c.public_bytes(Encoding.PEM).decode() for c in extra_certs)
            if extra_certs
            else ""
        )
        return cert_pem, key_pem, ca_pem

    # ── Internal HTTP helper ──────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        body: str = "{}",
        token: str | None = None,
    ) -> Any:
        """
        Make a signed API request and return the ``data`` field of the response.

        Raises:
            TyphurAuthError  – on credential / token failures (codes 1, 52, …)
            TyphurApiError   – on other non-zero response codes
        """
        if token is None:
            token = self._token or "none"

        headers = self._sign_headers(token, body)
        url = f"{self._api_base}{path}"

        assert self._session is not None
        async with self._session.request(
            method,
            url,
            headers=headers,
            data=body,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json()

        code = payload.get("code")
        if code == API_CODE_OK:
            return payload.get("data")

        msg = payload.get("msg", "unknown error")
        _LOGGER.debug("Typhur API %s %s → code=%s msg=%s", method, path, code, msg)

        if code in (API_CODE_TOKEN_EXPIRED, "1", "401"):
            self._token = None
            raise TyphurAuthError(f"Authentication failed (code {code}): {msg}")
        raise TyphurApiError(f"API error (code {code}): {msg}")
