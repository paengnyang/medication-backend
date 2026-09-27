import bcrypt


# ============================================================
# 비밀번호 암호화
# ============================================================

def hash_password(
    password: str
) -> str:

    password = str(
        password
    )

    if len(password) < 8:
        raise ValueError(
            "비밀번호는 8자 이상이어야 합니다."
        )

    password_hash = bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt()
    )

    return password_hash.decode(
        "utf-8"
    )


# ============================================================
# 비밀번호 검증
# ============================================================

def verify_password(
    password: str,
    password_hash: str,
) -> bool:

    if not password_hash:
        return False

    try:

        return bcrypt.checkpw(
            password.encode(
                "utf-8"
            ),
            password_hash.encode(
                "utf-8"
            ),
        )

    except (
        ValueError,
        TypeError,
    ):
        return False


# ============================================================
# 사용자 비밀번호 검증
# ============================================================

def verify_user_password(
    user_data: dict,
    input_password: str,
) -> bool:

    stored_hash = (
        user_data.get(
            "passwordHash"
        )
    )

    if not stored_hash:
        return False

    return verify_password(
        input_password,
        stored_hash,
    )