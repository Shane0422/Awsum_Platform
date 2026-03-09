# backend/routers/common.py
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from backend.config.messages import popup_multi_choice

router = APIRouter()

# ✅ 리다이렉트용 HTML (JS + 수동 링크 백업)
def _redirect_html(url: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <title>Redirecting...</title>
    </head>
    <body style="font-family: sans-serif; text-align:center; padding:50px;">
      <h2>Redirecting...</h2>
      <script>
        // ✅ JS 자동 이동
        window.location.replace("{url}");
      </script>
      <p><a href="{url}">Click here if not redirected</a></p>
    </body>
    </html>
    """

# ✅ Confirm Result 처리 (GET/POST 지원)
@router.api_route("/confirm-result", methods=["GET", "POST"])
async def confirm_result(request: Request):
    choice = 0
    referer = None

    if request.method == "POST":
        ctype = request.headers.get("content-type", "")
        if "application/json" in ctype:
            data = await request.json()
            choice = int(data.get("choice", 0) or 0)
            referer = data.get("referer")
        else:
            form = await request.form()
            choice = int(form.get("choice") or 0)
            referer = form.get("referer")
    else:
        qp = request.query_params
        choice = int(qp.get("choice") or 0)
        referer = qp.get("referer")

    # ✅ 선택지에 따른 목적지
    if choice == 1:
        redirect_url = referer or "/"
    elif choice == 2:
        redirect_url = "/"
    elif choice == 3:
        redirect_url = "/alt"
    else:
        redirect_url = "/"

    return HTMLResponse(content=_redirect_html(redirect_url), status_code=200)

# ✅ 샘플 라우트 (Task)
@router.get("/task")
def choose_task():
    html = popup_multi_choice(
        title="Task Selection",
        message="Please choose an action:",
        choices={1: "Save", 2: "Cancel"},
        redirect_path="/confirm-result"
    )
    return HTMLResponse(content=html)

# ✅ 샘플 라우트 (Multi)
@router.get("/multi")
def multi_choice():
    html = popup_multi_choice(
        title="Action Required",
        message="What do you want to do?",
        choices={1: "Save", 2: "Edit", 3: "Delete", 4: "Cancel"},
        redirect_path="/confirm-result"
    )
    return HTMLResponse(content=html)
