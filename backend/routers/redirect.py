# backend/routers/redirect.py
from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()

@router.get("/login")
def redirect_login():
    return RedirectResponse(url="/auth/login", status_code=307)

@router.get("/register")
def redirect_register():
    return RedirectResponse(url="/auth/register", status_code=307)
