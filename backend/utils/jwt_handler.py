# backend/utils/jwt_handler.py
from datetime import datetime, timedelta
from jose import jwt

# ✅ JWT 설정 (한 군데에서만 관리)
SECRET_KEY = "myjwtsecret"   # 반드시 안전하게 관리 (환경변수 권장)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# ✅ Access Token 생성
def create_access_token(data: dict, expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=expires_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
