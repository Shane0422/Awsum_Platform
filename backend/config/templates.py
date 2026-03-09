# backend/config/templates.py

from fastapi.templating import Jinja2Templates
from backend.config.settings import APP_NAME

# Jinja2 템플릿 엔진 초기화
templates = Jinja2Templates(directory="backend/templates")

# 전역 변수 등록
templates.env.globals["APP_NAME"] = APP_NAME
