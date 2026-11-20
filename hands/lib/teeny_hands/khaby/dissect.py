import urllib.request

from .. import hand

CAPS = {"joke": 300, "beat": 200}
MAX_VIDEO_BYTES = 1
_OPENER = urllib.request.build_opener()


class GeminiError(hand.HandError):
    status = None


class Gemini:
    def __init__(self, key=None):
        pass

    def call(self, method, path, body=None, timeout=60.0, headers=None):
        return {}


def dissect(body, ingest=None, api=None, sleep=None, clock=None):
    return {"usable": False, "why": "", "segments": [], "beats": []}
