
import os
from pathlib import Path
from dotenv import load_dotenv


load_dotenv()

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

    jwt_secret = os.getenv("JWT_SECRET", "on-budget-ai-admin-2026")
    scenarios_root = os.path.join(base_dir, "scenarios")
    openai_ssl = False if os.getenv("OPENAI_SSL", "0") == "0" else True

print(f"====== database type:      {Cfg.db_type} ======")
print(f"====== data base dir:      {Cfg.base_dir} ======")
print(f"====== scenarios root dir: {Cfg.scenarios_root} ======")
print(f"====== openai ssl: {Cfg.openai_ssl} ======")

