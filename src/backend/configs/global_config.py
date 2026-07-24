
import os
import secrets
import logging
from pathlib import Path
from dotenv import load_dotenv


load_dotenv()


def _development_jwt_secret_path() -> Path:
    """Return the local, Git-ignored location for the development signing key."""
    return Path(__file__).resolve().parent.parent / "data" / ".jwt_secret"


def _load_or_create_development_jwt_secret() -> str:
    """Reuse a local development key so restarting the API does not revoke tokens."""
    secret_path = _development_jwt_secret_path()
    try:
        secret = secret_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        secret = ""

    if secret:
        if len(secret.encode("utf-8")) < 32:
            raise ValueError(
                f"开发 JWT 密钥文件 {secret_path} 必须至少包含 32 个 UTF-8 字节。"
            )
        return secret

    secret = secrets.token_urlsafe(48)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        file_descriptor = os.open(
            secret_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        # A second development worker created the key first; use that shared key.
        secret = secret_path.read_text(encoding="utf-8").strip()
    else:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as secret_file:
            secret_file.write(f"{secret}\n")

    if len(secret.encode("utf-8")) < 32:
        raise ValueError(f"无法读取有效的开发 JWT 密钥文件：{secret_path}")
    return secret


def _load_jwt_secret() -> str:
    """Load a HS256-safe signing key, keeping development keys stable across restarts."""
    secret = os.getenv("JWT_SECRET", "")
    if secret:
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("JWT_SECRET 必须至少包含 32 个 UTF-8 字节，才能用于 HS256 签名。")
        return secret

    environment = os.getenv("ENV", "development").lower()
    if environment in {"production", "prod"}:
        raise ValueError("生产环境必须配置至少 32 字节的 JWT_SECRET。")

    logging.getLogger(__name__).warning(
        "JWT_SECRET 未配置；将使用本机 data/.jwt_secret 中持久化的开发签名密钥。"
    )
    return _load_or_create_development_jwt_secret()

class Cfg:
    project_name = os.getenv("PROJECT_NAME", "ontology-v2")

    log_name = os.getenv("LOG_NAME", "app")
    log_level = os.getenv("LOG_LEVEL", "INFO")
    log_format = os.getenv(
        "LOG_FORMAT",
        "%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    base_dir = os.path.join(str(Path(__file__).resolve().parent.parent), "data")
    
    db_type = os.getenv("DB_TYPE", "sqlite3")
    db_dsn = os.getenv("DATABASE_URL", "sqlite:///" + os.path.join(base_dir, "admin.db"))
    db_path = os.path.join(base_dir, "admin.db")

    jwt_secret = _load_jwt_secret()
    scenarios_root = os.path.join(base_dir, "scenarios")
    openai_ssl = False if os.getenv("OPENAI_SSL", "0") == "0" else True

print(f"====== database type:      {Cfg.db_type} ======")
print(f"====== data base dir:      {Cfg.base_dir} ======")
print(f"====== scenarios root dir: {Cfg.scenarios_root} ======")
print(f"====== openai ssl: {Cfg.openai_ssl} ======")

