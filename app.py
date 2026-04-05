from pathlib import Path
import json
import shutil
from datetime import datetime
import os
import logging
import time
import secrets
import base64
import hashlib
from typing import Optional
from fastapi import APIRouter, Body, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        # Avoid stale JS/CSS during local development.
        response.headers["Cache-Control"] = "no-store"
        return response


_log_level = (os.environ.get("SKILLBOTTLE_LOG_LEVEL") or "INFO").upper()
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("skillbottle")

app = FastAPI(title="SkillBottle Web")


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("ERR %s %s", request.method, request.url.path)
        raise

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    path = request.url.path
    if path == "/" or path == "/index.html" or path.startswith("/api"):
        logger.info("%s %s -> %s %.1fms", request.method, path, response.status_code, elapsed_ms)
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api")


ADMIN_PASSWORD_ENV = os.environ.get("SKILLBOTTLE_ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD") or ""
ADMIN_SECRET_FILE = Path(__file__).resolve().parent / ".skillbottle_admin.json"


def _admin_source() -> str:
    if ADMIN_PASSWORD_ENV:
        return "env"
    if ADMIN_SECRET_FILE.is_file():
        return "file"
    return "none"


def _load_admin_secret() -> Optional[dict]:
    try:
        if not ADMIN_SECRET_FILE.is_file():
            return None
        data = json.loads(ADMIN_SECRET_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        if data.get("algo") != "pbkdf2_sha256":
            return None
        if not isinstance(data.get("salt"), str) or not isinstance(data.get("dk"), str):
            return None
        if not isinstance(data.get("iterations"), int):
            return None
        return data
    except Exception:
        return None


def _derive_key(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def _hash_password(password: str) -> dict:
    salt = secrets.token_bytes(16)
    iterations = 200_000
    dk = _derive_key(password, salt, iterations)
    return {
        "algo": "pbkdf2_sha256",
        "salt": base64.b64encode(salt).decode("ascii"),
        "dk": base64.b64encode(dk).decode("ascii"),
        "iterations": iterations,
    }


def _verify_admin_password(password: str) -> bool:
    source = _admin_source()
    if source == "env":
        return secrets.compare_digest(password, ADMIN_PASSWORD_ENV)

    if source == "file":
        secret = _load_admin_secret()
        if not secret:
            return False
        try:
            salt = base64.b64decode(secret["salt"].encode("ascii"))
            dk_expected = base64.b64decode(secret["dk"].encode("ascii"))
            iterations = int(secret["iterations"])
        except Exception:
            return False

        dk = _derive_key(password, salt, iterations)
        return secrets.compare_digest(dk, dk_expected)

    return False


def _write_admin_password(password: str) -> None:
    secret = _hash_password(password)
    ADMIN_SECRET_FILE.write_text(json.dumps(secret, ensure_ascii=False, indent=2), encoding="utf-8")


@api.get("/admin/status")
def admin_status() -> dict:
    source = _admin_source()
    return {"configured": source != "none", "source": source}


@api.post("/admin/register")
def admin_register(payload: dict = Body(...)) -> dict:
    source = _admin_source()
    if source == "env":
        return {"ok": False, "reason": "已通过环境变量配置，无法在界面注册"}
    if source == "file":
        return {"ok": False, "reason": "管理员密码已设置"}

    password = str((payload or {}).get("password", ""))
    if len(password) < 4:
        return {"ok": False, "reason": "密码至少 4 位"}

    _write_admin_password(password)
    return {"ok": True}


@api.post("/admin/change")
def admin_change(payload: dict = Body(...)) -> dict:
    source = _admin_source()
    if source == "env":
        return {"ok": False, "reason": "已通过环境变量配置，无法在界面修改"}
    if source != "file":
        return {"ok": False, "reason": "管理员密码未配置"}

    old_password = str((payload or {}).get("old_password", ""))
    new_password = str((payload or {}).get("new_password", ""))

    if not _verify_admin_password(old_password):
        return {"ok": False, "reason": "原密码错误"}
    if len(new_password) < 4:
        return {"ok": False, "reason": "新密码至少 4 位"}

    _write_admin_password(new_password)
    return {"ok": True}


@api.post("/admin/verify")
def admin_verify(payload: dict = Body(...)) -> dict:
    source = _admin_source()
    password = str((payload or {}).get("password", ""))
    if source == "none":
        return {"ok": False, "reason": "管理员密码未配置"}
    return {"ok": _verify_admin_password(password)}


def _discover_apps(apps_dir: Path, href_prefix: str = "/apps") -> list[dict]:
    items: list[dict] = []
    if not apps_dir.exists():
        return items

    for child in apps_dir.iterdir():
        if not child.is_dir():
            continue
        index_html = child / "index.html"
        if not index_html.is_file():
            continue
        name = child.name
        prefix = href_prefix.rstrip("/")
        items.append({"id": name, "label": name, "href": f"{prefix}/{name}/index.html"})

    items.sort(key=lambda x: x["label"].lower())
    return items


@api.get("/health")
def health() -> dict:
    return {"ok": True}


@api.get("/meta")
def meta() -> dict:
    return {"title": "SkillBottle"}


@api.get("/nav")
def nav() -> dict:
    start = time.perf_counter()
    items = _discover_apps(apps_dir)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    logger.info("nav: %d items in %.1fms (apps_dir=%s)", len(items), elapsed_ms, str(apps_dir))
    return {"items": items}


def _export_static_site(project_root: Path, *, personalize: Optional[dict] = None, theme: Optional[str] = None) -> Path:
    apps_items = _discover_apps(apps_dir, href_prefix="apps")

    result_root = project_root / "result"
    result_root.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = result_root / f"export-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "apps").mkdir(parents=True, exist_ok=True)

    for item in apps_items:
        src = apps_dir / item["id"]
        dst = out_dir / "apps" / item["id"]
        shutil.copytree(src, dst, dirs_exist_ok=True)

    manifest = {"items": apps_items}
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    frontend_dir = project_root / "frontend"
    index_html = (frontend_dir / "index.html").read_text(encoding="utf-8")

    index_html = index_html.replace('href="/styles.css"', 'href="./styles.css"')
    index_html = index_html.replace('src="/app.js"', 'src="./app.js"')
    index_html = index_html.replace('href="/', 'href="./')
    index_html = index_html.replace('src="/', 'src="./')


    embeds = [
        '\n    <script type="application/json" id="sb-manifest">'
        + json.dumps(manifest, ensure_ascii=False)
        + "</script>\n"
    ]

    if isinstance(personalize, dict) and personalize:
        embeds.append(
            '    <script type="application/json" id="sb-personalize">'
            + json.dumps(personalize, ensure_ascii=False)
            + "</script>\n"
        )

    if theme in ("dark", "light"):
        embeds.append(
            '    <script type="application/json" id="sb-theme">'
            + json.dumps(theme)
            + "</script>\n"
        )

    embed = "".join(embeds)
    needle = '    <script src="./app.js"></script>'
    if needle in index_html:
        index_html = index_html.replace(needle, embed + needle, 1)

    (out_dir / "index.html").write_text(index_html, encoding="utf-8")
    shutil.copy2(frontend_dir / "styles.css", out_dir / "styles.css")
    shutil.copy2(frontend_dir / "app.js", out_dir / "app.js")

    return out_dir


@api.post("/export")
@api.get("/export")
def export(payload: dict = Body(default=None)) -> dict:
    payload = payload or {}
    out_dir = _export_static_site(
        Path(__file__).resolve().parent,
        personalize=payload.get("personalize"),
        theme=payload.get("theme"),
    )
    return {"ok": True, "out_dir": str(out_dir)}


app.include_router(api)

apps_dir = Path(__file__).resolve().parent / "app"
apps_dir.mkdir(parents=True, exist_ok=True)
app.mount("/apps", NoCacheStaticFiles(directory=str(apps_dir), html=True), name="apps")

frontend_dir = Path(__file__).resolve().parent / "frontend"
app.mount("/", NoCacheStaticFiles(directory=str(frontend_dir), html=True), name="frontend")


