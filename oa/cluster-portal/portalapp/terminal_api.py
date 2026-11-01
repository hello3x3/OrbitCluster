"""Terminal API. Browser Cookies are neither accepted nor issued here."""
from functools import wraps
import shlex

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.exceptions import HTTPException

from . import auth, terminal_auth

PREFIX = "/api/terminal/v1"


def success(data=None, message=None):
    payload = {"ok": True, "data": data}
    if message:
        payload["message"] = message
    return jsonify(payload)


def failure(message, code="operation_rejected", status=400):
    response = jsonify(ok=False, error=message, code=code)
    response.status_code = status
    if status == 401:
        response.headers["WWW-Authenticate"] = 'Bearer realm="OrbitCluster terminal"'
    return response


def register_terminal_api(app, get_db, callbacks=None):
    bp = Blueprint("terminal_api", __name__, url_prefix=PREFIX)

    @bp.before_request
    def secure_transport():
        if terminal_auth.require_https() and not request.is_secure:
            return failure("终端接口需要 HTTPS，请配置 HTTPS 地址及内部 CA", "https_required", 403)

    @bp.after_request
    def private_response(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response

    @bp.errorhandler(400)
    @bp.errorhandler(403)
    @bp.errorhandler(404)
    @bp.errorhandler(405)
    @bp.errorhandler(413)
    @bp.errorhandler(HTTPException)
    def http_error(exc):
        return failure("资源不存在" if exc.code == 404 else "请求无法处理", "http_error", exc.code)

    @bp.errorhandler(Exception)
    def unexpected_error(exc):
        if current_app.testing:
            raise exc
        current_app.logger.error("Terminal request failed", exc_info=True)
        return failure("服务器内部错误，请联系管理员", "server_error", 500)

    def authenticated(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return failure("请先在终端登录", "authentication_required", 401)
            raw = header[7:]
            result = terminal_auth.authenticate(get_db(), raw)
            if result is None:
                return failure("登录已过期或被撤销，请重新登录", "authentication_required", 401)
            g.terminal_user, g.terminal_credential = result
            g.terminal_bearer_verified = True
            g.terminal_raw_token = raw
            return func(*args, **kwargs)
        return wrapped

    @bp.post("/auth/login")
    def login():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return failure("登录参数必须为 JSON 对象", "invalid_parameters")
        username, password = data.get("username"), data.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            return failure("请填写用户名和密码", "invalid_parameters")
        username = username.strip().lower()
        device = data.get("device", "")
        if not username or len(username) > 32 or len(password) > 1024 or not isinstance(device, str):
            return failure("登录参数格式错误", "invalid_parameters")
        db = get_db()
        buckets = terminal_auth.login_buckets(username, request.remote_addr)
        retry = terminal_auth.retry_after(db, buckets)
        if retry:
            response = failure("登录失败次数过多，请稍后重试", "rate_limited", 429)
            response.headers["Retry-After"] = str(retry)
            return response
        user = db.user_by_name(username)
        if user is None or not auth.verify_password(password, user["passwd"]) or not user["is_active"]:
            terminal_auth.record_failure(db, buckets)
            return failure("用户名或密码错误，或账号已停用", "invalid_credentials", 401)
        credential = terminal_auth.issue(db, user, device)
        if credential is None:
            return failure("账号状态已变化，请重新登录", "invalid_credentials", 401)
        db.exec("DELETE FROM terminal_login_failures WHERE bucket=?", (buckets[0][0],))
        db.user_touch_login(user["id"])
        return success(credential)

    @bp.get("/auth/me")
    @authenticated
    def me():
        u, credential = g.terminal_user, g.terminal_credential
        return success({"username": u["username"], "display_name": u["display_name"],
                        "issued_at": credential["issued_at"], "expires_at": credential["expires_at"]})

    @bp.post("/auth/logout")
    @authenticated
    def logout():
        terminal_auth.revoke(get_db(), g.terminal_raw_token)
        return success(message="当前终端登录已注销")

    if callbacks:
        # Keep browser and terminal behavior in the same business handlers.
        # No endpoint name or user identity can be chosen by the caller.
        legacy_routes = [
            ("POST", "/instances", "apply_submit"),
            ("GET", "/instances/<int:iid>/log", "instance_log"),
            ("POST", "/instances/<int:iid>/stop", "instance_stop"),
            ("POST", "/instances/<int:iid>/restart", "instance_restart"),
            ("POST", "/instances/<int:iid>/save-image", "instance_save_image"),
            ("POST", "/instances/<int:iid>/delete", "instance_delete"),
            ("POST", "/images/<name>/rename", "image_rename"),
            ("POST", "/images/<name>/delete", "image_delete"),
            ("POST", "/images/<name>/comment", "image_comment"),
            ("POST", "/profile/name", "profile_name"),
            ("POST", "/profile/password", "profile_password"),
            ("POST", "/profile/keys", "profile_key_add"),
            ("POST", "/profile/keys/<int:kid>/delete", "profile_key_del"),
        ]

        def adapt_legacy(endpoint):
            def invoke(**kwargs):
                response = app.make_response(app.view_functions[endpoint](**kwargs))
                payload = response.get_json(silent=True)
                if not isinstance(payload, dict):
                    return failure("服务器响应格式错误", "invalid_response", 502)
                if payload.get("ok") is False:
                    result = failure(payload.get("error", "操作失败"))
                    if payload.get("need_force"):
                        result = jsonify(ok=False, error=payload["error"],
                                         code="overwrite_confirmation_required", need_force=True)
                        result.status_code = 409
                    return result
                data = {k: v for k, v in payload.items() if k not in ("ok", "msg")}
                return success(data, payload.get("msg"))
            return invoke

        for method, path, endpoint in legacy_routes:
            bp.add_url_rule(path, endpoint="user_" + endpoint,
                view_func=authenticated(adapt_legacy(endpoint)), methods=[method])

        def public_instance(row):
            # Internal sbatch commands and log filesystem paths are not UI data.
            return {k: v for k, v in row.items() if k not in ("cmd", "log_path", "user_id")}

        def read_adapter(name):
            def invoke(**kwargs):
                result = callbacks[name](**kwargs)
                if name == "instances":
                    result = [public_instance(row) for row in result]
                elif name == "instance":
                    result = public_instance(result)
                return success(result)
            return invoke

        for path, name in [("/instances", "instances"), ("/instances/<int:iid>", "instance"),
                           ("/plans", "plans"), ("/images", "images"), ("/nodes", "nodes"),
                           ("/quota", "quota"), ("/profile", "profile"), ("/accounts", "accounts")]:
            bp.add_url_rule(path, endpoint="read_" + name,
                            view_func=authenticated(read_adapter(name)), methods=["GET"])

        @bp.get("/instances/<int:iid>/connection")
        @authenticated
        def connection(iid):
            row = callbacks["instance"](iid)
            ready = (row["state"] == "RUNNING" and not row["starting"]
                     and not row["startup_error"] and bool(row["ssh_ip"]))
            argv = ["ssh", "-p", str(row["port"]), row["username"] + "@" + row["ssh_ip"]] if ready else []
            return success({"id": iid, "job_id": row["job_id"], "ready": ready,
                            "state": row["state"], "state_cn": row["state_cn"],
                            "argv": argv, "command": shlex.join(argv) if argv else "",
                            "error": row["startup_error"]})

    app.register_blueprint(bp)
