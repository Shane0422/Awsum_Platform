# backend/config/settings.py

import os

APP_NAME = "AwsumSolution"

# 플랫폼 PostgreSQL 기본값 (환경 변수로 재정의 가능)
PLATFORM_DB_URL = os.getenv(
	"AWSUM_PLATFORM_DATABASE_URL",
	os.getenv("PLATFORM_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/awsum_platform"),
)
