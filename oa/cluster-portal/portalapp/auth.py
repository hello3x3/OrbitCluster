# -*- coding: utf-8 -*-
"""认证与安全小工具：口令哈希(PBKDF2)、CSRF token、session 工具。"""
import hashlib
import hmac
import os
import secrets

PBKDF2_ITER = 260000
_CSRF_LEN = 24


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITER)
    return "pbkdf2$%d$%s$%s" % (PBKDF2_ITER,
                                salt.hex(), dk.hex())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iter_s, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(iter_s))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def random_password(length=12) -> str:
    """人类友好随机密码（避免易混淆字符）。"""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def new_csrf() -> str:
    return secrets.token_hex(_CSRF_LEN)


def csrf_ok(session_csrf, posted_csrf) -> bool:
    if not session_csrf or not posted_csrf:
        return False
    return hmac.compare_digest(str(session_csrf), str(posted_csrf))


def validate_pubkey(pubkey: str):
    """校验 OpenSSH 公钥格式，返回 (ok, err)。"""
    import re
    pat = re.compile(
        r"^(ssh-(rsa|ed25519)|ecdsa-sha2-nistp(256|384|521)|"
        r"sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com) "
        r"[A-Za-z0-9+/]{20,}={0,2}( [^\r\n]{0,200})?$")
    s = (pubkey or "").strip()
    if not pat.match(s):
        return False, "公钥格式不正确：应为 OpenSSH 格式（如 ssh-ed25519 AAAA... comment），一行一条"
    if "\n" in s or "\r" in s:
        return False, "只允许一条公钥"
    return True, ""
