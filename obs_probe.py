"""Read-only OBS WebSocket 5 screenshot probe; no game input."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import time
import uuid


def authentication(password, salt, challenge):
    def digest(value):
        return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()
    return digest(digest(password + salt) + challenge)


class OBS:
    def __init__(self, url, password):
        import websocket
        self.ws = websocket.create_connection(url, timeout=5)
        try:
            hello = self.receive()
            if hello.get("op") != 0:
                raise RuntimeError("Expected OBS Hello")
            data = {"rpcVersion": 1, "eventSubscriptions": 0}
            auth = hello["d"].get("authentication")
            if auth:
                if not password:
                    raise RuntimeError("Set OBS_PASSWORD environment variable")
                data["authentication"] = authentication(password, auth["salt"], auth["challenge"])
            self.send(1, data)
            if self.receive().get("op") != 2:
                raise RuntimeError("OBS identification failed")
        except BaseException:
            self.close()
            raise

    def send(self, opcode, data):
        self.ws.send(json.dumps({"op": opcode, "d": data}))

    def receive(self):
        return json.loads(self.ws.recv())

    def request(self, kind, data=None):
        request_id = str(uuid.uuid4())
        self.send(6, {"requestType": kind, "requestId": request_id, "requestData": data or {}})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.ws.settimeout(max(0.01, deadline - time.monotonic()))
            message = self.receive()
            response = message.get("d", {})
            if message.get("op") != 7 or response.get("requestId") != request_id:
                continue
            status = response["requestStatus"]
            if not status["result"]:
                raise RuntimeError(f"{kind}: {status}")
            return response.get("responseData", {})
        raise TimeoutError(kind)

    def close(self):
        self.ws.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("OBS_URL", "ws://127.0.0.1:4455"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--source")
    parser.add_argument("--output", type=Path, default=Path("frame.png"))
    args = parser.parse_args()
    obs = OBS(args.url, os.environ.get("OBS_PASSWORD", ""))
    try:
        if args.list:
            print(json.dumps({"inputs": obs.request("GetInputList"),
                              "scenes": obs.request("GetSceneList")}, ensure_ascii=False, indent=2))
        else:
            start = time.monotonic()
            result = obs.request("GetSourceScreenshot", {"sourceName": args.source, "imageFormat": "png"})
            elapsed = time.monotonic() - start
            header, encoded = result["imageData"].split(",", 1)
            if header != "data:image/png;base64":
                raise ValueError("Unexpected screenshot format")
            image = base64.b64decode(encoded, validate=True)
            if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Invalid PNG payload")
            with args.output.open("xb") as output:
                output.write(image)
            print(json.dumps({"path": str(args.output.resolve()), "request_ms": round(elapsed * 1000, 1),
                              "note": "Request time is not end-to-end game latency"}, indent=2))
    finally:
        obs.close()


if __name__ == "__main__":
    main()
