#!/usr/bin/env python3
"""Opt-in obs-websocket v5 probe for the phase 2 scene-provider trial.

Proves the boundary the `stream_control` `websocket` scene provider needs:
  Hello (op 0) -> Identify (op 1, with the obs-websocket v5 auth string) -> Identified (op 2)
  -> GetSceneList (op 6) -> RequestResponse (op 7)

Reads everything from the environment (never from the repository):
  OBS_WEBSOCKET_URL       default ws://127.0.0.1:4455
  OBS_WEBSOCKET_PASSWORD  required
Run with the project virtualenv: `.venv/bin/python scripts/obs_trial_probe.py`
Exit code 0 = the provider answered with a scene list; 2 = skipped (no URL/password), 1 = failure.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import sys

import aiohttp

OP_HELLO, OP_IDENTIFY, OP_IDENTIFIED, OP_REQUEST, OP_RESPONSE = 0, 1, 2, 6, 7


def _auth_string(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


async def main() -> int:
    url = os.environ.get("OBS_WEBSOCKET_URL", "ws://127.0.0.1:4455")
    password = os.environ.get("OBS_WEBSOCKET_PASSWORD", "")
    if not password:
        print("SKIP: OBS_WEBSOCKET_PASSWORD is unset (the trial is opt-in)")
        return 2

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            hello = json.loads((await ws.receive(timeout=10)).data)
            assert hello["op"] == OP_HELLO, hello
            info = hello["d"].get("authentication") or {}
            payload: dict = {"rpcVersion": hello["d"]["rpcVersion"], "eventSubscriptions": 0}
            if info:
                payload["authentication"] = _auth_string(password, info["salt"], info["challenge"])
            await ws.send_str(json.dumps({"op": OP_IDENTIFY, "d": payload}))
            msg = await ws.receive(timeout=10)
            if msg.type is not aiohttp.WSMsgType.TEXT:
                print(f"FAIL: server closed after Identify (type={msg.type!r}, data={msg.data!r}, extra={msg.extra!r})")
                return 1
            ident = json.loads(msg.data)
            if ident["op"] != OP_IDENTIFIED:
                print("FAIL: identify refused:", json.dumps(ident)[:300])
                return 1
            print("Identified with obs-websocket", hello["d"].get("obsWebSocketVersion"),
                  "| negotiated RPC", ident["d"].get("negotiatedRpcVersion"))

            res = await request(ws, "GetSceneList", "probe-1")
            if res is None:
                return 1
            scenes = res.get("scenes", [])
            current = res.get("currentProgramSceneName")
            print("GetSceneList OK | scenes:", [s.get("sceneName") for s in scenes],
                  "| current:", current)
            if not scenes:
                print("FAIL: no scene to set")
                return 1

            target = os.environ.get("OBS_TRIAL_SCENE", "").strip()
            if not target:
                print("(set OBS_TRIAL_SCENE=<name> to also exercise set + read-back)")
                return 0
            if target not in [s.get("sceneName") for s in scenes]:
                if await request(ws, "CreateScene", "probe-2", {"sceneName": target}) is None:
                    return 1
                print("CreateScene OK |", target)
            if await request(ws, "SetCurrentProgramScene", "probe-3",
                             {"sceneName": target}) is None:
                return 1
            back = await request(ws, "GetCurrentProgramScene", "probe-4")
            if back is None:
                return 1
            got = back.get("currentProgramSceneName")
            print("SetCurrentProgramScene + read-back:", got)
            return 0 if got == target else 1


async def request(ws, request_type: str, request_id: str, data: dict | None = None):
    """One obs-websocket v5 request (op 6) -> its responseData (op 7), None on refusal."""
    payload: dict = {"requestType": request_type, "requestId": request_id}
    if data:
        payload["requestData"] = data
    await ws.send_str(json.dumps({"op": OP_REQUEST, "d": payload}))
    msg = await ws.receive(timeout=10)
    if msg.type is not aiohttp.WSMsgType.TEXT:
        print(f"FAIL: closed during {request_type} (code={msg.data!r} {msg.extra!r})")
        return None
    resp = json.loads(msg.data)
    if resp.get("op") != OP_RESPONSE:
        print(f"FAIL: unexpected op {resp.get('op')} for {request_type}")
        return None
    d = resp["d"]
    if not d.get("requestStatus", {}).get("result"):
        print(f"FAIL: {request_type} refused:", json.dumps(d)[:300])
        return None
    return d.get("responseData", {})


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
