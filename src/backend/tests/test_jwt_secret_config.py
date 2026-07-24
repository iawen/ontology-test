import os
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from configs.global_config import _load_jwt_secret


class JwtSecretConfigTests(unittest.TestCase):
    def test_development_fallback_is_persisted_and_meets_hs256_minimum_length(self):
        with tempfile.TemporaryDirectory() as directory:
            secret_path = Path(directory) / ".jwt_secret"
            config_module = importlib.import_module(_load_jwt_secret.__module__)
            with patch.object(
                config_module, "_development_jwt_secret_path", return_value=secret_path
            ), patch.dict(os.environ, {"JWT_SECRET": "", "ENV": "development"}, clear=False):
                first_secret = _load_jwt_secret()
                second_secret = _load_jwt_secret()

            self.assertGreaterEqual(len(first_secret.encode("utf-8")), 32)
            self.assertEqual(first_secret, second_secret)
            self.assertEqual(secret_path.read_text(encoding="utf-8").strip(), first_secret)

    def test_short_configured_secret_is_rejected(self):
        with patch.dict(os.environ, {"JWT_SECRET": "too-short", "ENV": "development"}, clear=False):
            with self.assertRaisesRegex(ValueError, "至少包含 32"):
                _load_jwt_secret()

    def test_production_requires_explicit_secret(self):
        with patch.dict(os.environ, {"JWT_SECRET": "", "ENV": "production"}, clear=False):
            with self.assertRaisesRegex(ValueError, "生产环境"):
                _load_jwt_secret()


if __name__ == "__main__":
    unittest.main()