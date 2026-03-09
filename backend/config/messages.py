from fastapi import Request
import json

# ==========================
# 메시지 정의
# ==========================
MESSAGES = {
    "error": {
        401: "Unauthorized access.",
        403: "Forbidden: You don't have permission.",
        404: "Page not found.",
        500: "Internal server error.",
    }
}
DEFAULT_ERROR = "An unexpected error occurred."


def get_error_message(status_code: int) -> str:
    return MESSAGES["error"].get(status_code, DEFAULT_ERROR)


# ==========================
# SweetAlert2 Confirm 팝업
# ==========================
def popup_multi_choice(
    title: str = "Notice",
    message: str = "",
    choices: dict | None = None,
    payload: dict | None = None,
    redirect_path: str | None = "/confirm-result",
    icon: str = "question",
) -> str:
    """
    SweetAlert2 기반 다중 선택 팝업 생성
    - choices: {번호: "라벨"}
    - redirect_path: 선택 후 POST할 API (없으면 fetch 생략)
    """
    if not choices:
        choices = {1: "OK"}

    j = lambda v: json.dumps(v, ensure_ascii=False)

    btns = [{"key": k, "label": v} for k, v in choices.items()]
    btns_json = j(btns)
    extra_payload = j(payload or {})

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script>
        <style>
          .tr-btn {{
            margin:6px; padding:10px 24px; font-size:15px; border-radius:6px;
            border:none; cursor:pointer; min-width:90px;
          }}
        </style>
    </head>
    <body>
      <script>
        const btns = {btns_json};

        Swal.fire({{
            title: {j(title)},
            text: {j(message)},
            icon: {j(icon)},
            showConfirmButton: false,
            showCancelButton: false,
            html: btns.map((b, idx) => {{
                let color = "#3498db"; 
                if (idx === btns.length - 1) color = "#95a5a6"; 
                if (b.label.toLowerCase().includes("delete") || b.label.includes("삭제"))
                    color = "#e74c3c";

                // 🔑 Home 버튼은 fetch 거치지 않고 즉시 이동
                if (b.label.toLowerCase().includes("home")) {{
                    return `<button class="tr-btn" style="background:${{color}};color:#fff;" onclick="window.location.href='/'">${{b.label}}</button>`;
                }}

                return `<button class="tr-btn" style="background:${{color}};color:#fff;" onclick="sendChoice(${{b.key}})">${{b.label}}</button>`;
            }}).join(" ")
        }});

        function sendChoice(choice) {{
            if (!{j(bool(redirect_path))}) {{
                Swal.close();
                return;
            }}
            const body = Object.assign({{ choice }}, {extra_payload});
            fetch({j(redirect_path)}, {{
                method: "POST",
                headers: {{ "Content-Type": "application/json" }},
                body: JSON.stringify(body)
            }}).then(res => res.json())
              .then(data => {{
                  if (data.redirect) window.location.href = data.redirect;
              }});
        }}
      </script>
    </body>
    </html>
    """


# ==========================
# 에러 팝업 빌더
# ==========================
def build_error_popup(status_code: int, request: Request) -> str:
    msg = get_error_message(status_code)
    if status_code in (401, 403, 404):
        referer = request.headers.get("referer", "/")
        return popup_multi_choice(
            title="Notice",
            message=msg,
            choices={1: "Go Home", 2: "Cancel"},
            payload={"referer": referer},
            redirect_path="/confirm-result",
            icon="warning" if status_code == 404 else "error",
        )
    return popup_multi_choice(
        title="Notice",
        message=msg,
        choices={1: "OK"},
        redirect_path=None,
        icon="error",
    )
