import unittest
import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import patch

from scout.auth import BasicAuth


class AuthTests(unittest.TestCase):
    def test_missing_password_fails_closed(self):
        middleware = BasicAuth(app=None)
        async def next_response(request):
            return SimpleNamespace(status_code=200)
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})
        with patch.dict("os.environ", {"APP_PASSWORD": ""}):
            response = asyncio.run(middleware.dispatch(request, next_response))
        self.assertEqual(response.status_code, 503)

    def test_loopback_origin_still_requires_password(self):
        middleware = BasicAuth(app=None)
        async def next_response(request):
            return SimpleNamespace(status_code=200)
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})
        with patch.dict("os.environ", {"APP_PASSWORD": "test-secret", "APP_USER": "ivan"}):
            denied = asyncio.run(middleware.dispatch(request, next_response))
            token = base64.b64encode(b"ivan:test-secret").decode("ascii")
            request.headers = {"authorization": "Basic " + token}
            allowed = asyncio.run(middleware.dispatch(request, next_response))
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(allowed.status_code, 200)


if __name__ == "__main__":
    unittest.main()
