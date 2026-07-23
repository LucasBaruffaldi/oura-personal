from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
import time
import webbrowser
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
API_BASE_URL = "https://api.ouraring.com/v2/usercollection"
DEFAULT_REDIRECT_URI = "http://localhost:8000/callback"
TOKEN_FILE = Path(".oura_tokens.json")

# "date" usa start_date/end_date; "datetime" usa start_datetime/end_datetime.
# "collection" pagina sin filtro temporal y "single" devuelve un solo documento.
ENDPOINTS: dict[str, str] = {
    "personal_info": "single",
    "daily_activity": "date",
    "daily_readiness": "date",
    "daily_sleep": "date",
    "sleep": "date",
    "sleep_time": "date",
    "heartrate": "datetime",
    "workout": "date",
    "session": "date",
    "tag": "date",
    "enhanced_tag": "date",
    "daily_spo2": "date",
    "daily_stress": "date",
    "daily_resilience": "date",
    "daily_cardiovascular_age": "date",
    "vO2_max": "date",
    "rest_mode_period": "date",
    "ring_configuration": "collection",
    "ring_battery_level": "datetime",
}


class OuraHTTPError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def load_env_file(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            os.environ.setdefault(key, value)


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
) -> Any:
    if params:
        url = f"{url}?{urlencode(params)}"

    request_headers = dict(headers or {})
    body = None
    if form is not None:
        body = urlencode(form).encode("utf-8")
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
            detail = payload.get("detail") or payload.get("message") or str(payload)
        except (ValueError, AttributeError):
            detail = raw[:500] or exc.reason
        raise OuraHTTPError(exc.code, str(detail)) from exc
    except URLError as exc:
        raise RuntimeError(f"No se pudo conectar con Oura: {exc.reason}") from exc


def require_config() -> tuple[str, str, str]:
    load_env_file()
    client_id = os.getenv("OURA_CLIENT_ID", "").strip()
    client_secret = os.getenv("OURA_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("OURA_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip()

    missing = [
        name
        for name, value in {
            "OURA_CLIENT_ID": client_id,
            "OURA_CLIENT_SECRET": client_secret,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Faltan {', '.join(missing)} en el archivo .env. "
            "Copiá .env.example como .env y completalo."
        )
    return client_id, client_secret, redirect_uri


def build_authorization_url(
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: str = "",
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    # Oura solicita todos los scopes habilitados en la aplicación si se omite
    # este parámetro. Así también se admiten scopes nuevos del portal.
    if scopes.strip():
        params["scope"] = scopes.strip()
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def save_tokens(payload: dict[str, Any], previous: dict[str, Any] | None = None) -> None:
    merged = dict(previous or {})
    merged.update(payload)
    now = int(time.time())
    merged["obtained_at"] = now
    merged["expires_at"] = now + int(merged.get("expires_in", 0))

    temporary = TOKEN_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(TOKEN_FILE)


def load_tokens() -> dict[str, Any]:
    if not TOKEN_FILE.exists():
        raise RuntimeError(
            f"No existe {TOKEN_FILE}. Ejecutá primero: python oura_export.py auth"
        )
    return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))


def exchange_code(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict[str, Any]:
    return request_json(
        "POST",
        TOKEN_URL,
        form={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )


def refresh_tokens(
    tokens: dict[str, Any],
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise RuntimeError(
            "No hay refresh token. Volvé a ejecutar: python oura_export.py auth"
        )

    refreshed = request_json(
        "POST",
        TOKEN_URL,
        form={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    save_tokens(refreshed, previous=tokens)
    return load_tokens()


def valid_access_token(
    client_id: str,
    client_secret: str,
) -> str:
    tokens = load_tokens()
    expires_at = int(tokens.get("expires_at", 0))
    access_token = tokens.get("access_token")

    if access_token and expires_at > int(time.time()) + 120:
        return str(access_token)

    print("El access token venció o está por vencer; renovándolo...")
    tokens = refresh_tokens(tokens, client_id, client_secret)
    return str(tokens["access_token"])


def authorize() -> None:
    client_id, client_secret, redirect_uri = require_config()
    parsed = urlparse(redirect_uri)

    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise RuntimeError(
            "Este proyecto espera una redirect URI local como "
            f"{DEFAULT_REDIRECT_URI}."
        )
    if not parsed.port:
        raise RuntimeError("La redirect URI debe incluir un puerto, por ejemplo 8000.")

    expected_path = parsed.path or "/"
    expected_state = secrets.token_urlsafe(32)
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            request = urlparse(self.path)
            if request.path != expected_path:
                self.send_response(404)
                self.end_headers()
                return

            query = parse_qs(request.query)
            result["state"] = query.get("state", [""])[0]
            result["code"] = query.get("code", [""])[0]
            result["error"] = query.get("error", [""])[0]

            success = bool(result["code"]) and result["state"] == expected_state
            self.send_response(200 if success else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if success:
                body = (
                    "<h1>Autorización recibida</h1>"
                    "<p>Ya podés cerrar esta pestaña y volver a la terminal.</p>"
                )
            else:
                body = (
                    "<h1>No se pudo autorizar</h1>"
                    "<p>Volvé a la terminal para ver el detalle.</p>"
                )
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    bind_host = "127.0.0.1"
    try:
        server = HTTPServer((bind_host, parsed.port), CallbackHandler)
    except OSError as exc:
        raise RuntimeError(
            f"No se pudo abrir el puerto {parsed.port}. "
            "Cerrá cualquier programa que lo esté usando o elegí otro puerto "
            "tanto en Oura como en .env."
        ) from exc

    authorization_url = build_authorization_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=expected_state,
        scopes=os.getenv("OURA_SCOPES", ""),
    )

    print("Abriendo Oura en el navegador...")
    print("Si el navegador no se abre, visitá esta URL:\n")
    print(authorization_url)
    webbrowser.open(authorization_url)

    deadline = time.monotonic() + 300
    server.timeout = 1
    while not result and time.monotonic() < deadline:
        server.handle_request()
    server.server_close()

    if not result:
        raise RuntimeError("La autorización expiró después de 5 minutos.")
    if result.get("error"):
        raise RuntimeError(f"Oura rechazó la autorización: {result['error']}")
    if result.get("state") != expected_state:
        raise RuntimeError("El parámetro state no coincide; se canceló por seguridad.")
    if not result.get("code"):
        raise RuntimeError("Oura no devolvió un código de autorización.")

    tokens = exchange_code(
        code=result["code"],
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
    )
    save_tokens(tokens)
    print(f"\nAutorización completada. Tokens guardados en {TOKEN_FILE}.")


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Fecha inválida: {value}. Usá el formato AAAA-MM-DD."
        ) from exc


def resolve_period(
    days: int,
    start: date | None,
    end: date | None,
) -> tuple[date, date]:
    if (start is None) != (end is None):
        raise ValueError("--start y --end deben utilizarse juntos.")
    if start is not None and end is not None:
        if start > end:
            raise ValueError("--start no puede ser posterior a --end.")
        return start, end
    if days < 1:
        raise ValueError("--days debe ser 1 o mayor.")
    resolved_end = datetime.now(timezone.utc).date()
    return resolved_end - timedelta(days=days - 1), resolved_end


def endpoint_params(kind: str, start: date, end: date) -> dict[str, str]:
    if kind == "date":
        return {"start_date": start.isoformat(), "end_date": end.isoformat()}
    if kind == "datetime":
        return {
            "start_datetime": f"{start.isoformat()}T00:00:00Z",
            "end_datetime": f"{end.isoformat()}T23:59:59Z",
        }
    return {}


def get_endpoint(
    endpoint: str,
    kind: str,
    access_token: str,
    start: date,
    end: date,
) -> Any:
    url = f"{API_BASE_URL}/{endpoint}"
    base_params = endpoint_params(kind, start, end)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    if kind == "single":
        return request_json("GET", url, headers=headers)

    all_documents: list[Any] = []
    next_token: str | None = None
    while True:
        params = dict(base_params)
        if next_token:
            params["next_token"] = next_token
        payload = request_json("GET", url, headers=headers, params=params)

        documents = payload.get("data", [])
        if not isinstance(documents, list):
            raise RuntimeError(f"Respuesta inesperada del endpoint {endpoint}.")
        all_documents.extend(documents)

        next_token = payload.get("next_token")
        if not next_token:
            break

    return {"data": all_documents, "next_token": None}


def download(
    days: int,
    start: date | None,
    end: date | None,
    output_root: Path,
    only: list[str] | None,
) -> None:
    client_id, client_secret, _redirect_uri = require_config()
    access_token = valid_access_token(client_id, client_secret)
    resolved_start, resolved_end = resolve_period(days, start, end)

    selected = list(ENDPOINTS)
    if only:
        unknown = sorted(set(only) - set(ENDPOINTS))
        if unknown:
            raise ValueError(
                "Endpoints desconocidos: "
                + ", ".join(unknown)
                + ". Opciones: "
                + ", ".join(ENDPOINTS)
            )
        selected = only

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = output_root / f"oura_{resolved_start}_{resolved_end}_{stamp}"
    destination.mkdir(parents=True, exist_ok=False)

    summary: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "start_date": resolved_start.isoformat(),
        "end_date": resolved_end.isoformat(),
        "destination": str(destination),
        "endpoints": {},
    }

    print(
        f"Descargando {len(selected)} conjuntos entre "
        f"{resolved_start} y {resolved_end}..."
    )
    for endpoint in selected:
        kind = ENDPOINTS[endpoint]
        try:
            payload = get_endpoint(
                endpoint=endpoint,
                kind=kind,
                access_token=access_token,
                start=resolved_start,
                end=resolved_end,
            )
            output_file = destination / f"{endpoint}.json"
            output_file.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            count = (
                len(payload.get("data", []))
                if isinstance(payload, dict) and isinstance(payload.get("data"), list)
                else 1
            )
            summary["endpoints"][endpoint] = {
                "status": "ok",
                "documents": count,
                "file": output_file.name,
            }
            print(f"  OK   {endpoint}: {count}")
        except OuraHTTPError as exc:
            message = str(exc)
            summary["endpoints"][endpoint] = {
                "status": "error",
                "error": message,
            }
            print(f"  ERROR {endpoint}: {message}", file=sys.stderr)
        except Exception as exc:  # Mantiene la exportación de los demás conjuntos.
            summary["endpoints"][endpoint] = {
                "status": "error",
                "error": str(exc),
            }
            print(f"  ERROR {endpoint}: {exc}", file=sys.stderr)

    summary_file = destination / "_summary.json"
    summary_file.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nExportación terminada: {destination.resolve()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Autoriza y descarga datos personales desde Oura API V2."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("auth", help="Autorizar la cuenta Oura mediante OAuth.")

    download_parser = subparsers.add_parser(
        "download",
        help="Descargar datos en archivos JSON.",
    )
    download_parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Cantidad de días, incluido hoy (predeterminado: 30).",
    )
    download_parser.add_argument("--start", type=parse_iso_date)
    download_parser.add_argument("--end", type=parse_iso_date)
    download_parser.add_argument(
        "--output",
        type=Path,
        default=Path("exports"),
        help="Carpeta base de las exportaciones (predeterminado: exports).",
    )
    download_parser.add_argument(
        "--only",
        nargs="+",
        help="Descargar solamente los endpoints indicados.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "auth":
            authorize()
        else:
            download(
                days=args.days,
                start=args.start,
                end=args.end,
                output_root=args.output,
                only=args.only,
            )
        return 0
    except (RuntimeError, ValueError, socket.error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
