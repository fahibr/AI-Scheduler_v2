"""Simple email-only login with optional Supabase user store + region access."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

import streamlit as st

from .regions import (
    REGION_CODES,
    REGIONS,
    normalize_region_code,
    normalize_region_list,
    region_label,
)
from .supabase_client import get_supabase_client, supabase_configured

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

SESSION_EMAIL_KEY = "auth_email"
SESSION_ROLE_KEY = "auth_role"
SESSION_REGIONS_KEY = "auth_regions"
SESSION_ALL_REGIONS_KEY = "auth_access_all_regions"
SESSION_ACTIVE_REGION_KEY = "auth_active_region"
SESSION_FIRST_NAME_KEY = "auth_first_name"
SESSION_LAST_NAME_KEY = "auth_last_name"

DEFAULT_ADMIN_EMAIL = "fahmi.ibrahim@assaabloy.com"


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def is_valid_email(email: str | None) -> bool:
    value = normalize_email(email)
    return bool(value) and bool(_EMAIL_RE.fullmatch(value))


def _split_env_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts: list[str] = []
    for chunk in re.split(r"[,;\s]+", raw.strip()):
        item = chunk.strip().lower()
        if item:
            parts.append(item)
    return parts


def allowed_emails() -> set[str]:
    return {normalize_email(e) for e in _split_env_list(os.getenv("ALLOWED_EMAILS"))}


def allowed_domains() -> set[str]:
    domains: set[str] = set()
    for item in _split_env_list(os.getenv("ALLOWED_EMAIL_DOMAINS")):
        domains.add(item.lstrip("@"))
    return domains


def auth_backend() -> str:
    """Return 'supabase' when DB auth is configured, else 'env'."""
    return "supabase" if supabase_configured() else "env"


def auth_is_configured() -> bool:
    if auth_backend() == "supabase":
        return True
    return bool(allowed_emails() or allowed_domains())


def _env_email_is_allowed(email: str) -> bool:
    value = normalize_email(email)
    if not is_valid_email(value):
        return False
    emails = allowed_emails()
    domains = allowed_domains()
    if not emails and not domains:
        return True
    if value in emails:
        return True
    domain = value.rsplit("@", 1)[-1]
    return domain in domains


def _fetch_user(email: str) -> dict[str, Any] | None:
    client = get_supabase_client()
    result = (
        client.table("app_users")
        .select(
            "id,email,first_name,last_name,role,is_active,access_all_regions,regions,"
            "created_at,last_login_at,created_by"
        )
        .eq("email", normalize_email(email))
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def _record_login_event(
    email: str,
    *,
    success: bool,
    detail: str | None = None,
) -> None:
    if auth_backend() != "supabase":
        return
    try:
        client = get_supabase_client()
        user_agent = None
        try:
            headers = st.context.headers  # type: ignore[attr-defined]
            user_agent = headers.get("User-Agent") if headers else None
        except Exception:  # noqa: BLE001
            user_agent = None
        client.table("login_events").insert(
            {
                "email": normalize_email(email),
                "success": success,
                "detail": detail,
                "user_agent": (user_agent or "")[:500] or None,
            }
        ).execute()
    except Exception:  # noqa: BLE001
        pass


def _touch_last_login(email: str) -> None:
    if auth_backend() != "supabase":
        return
    try:
        client = get_supabase_client()
        client.table("app_users").update(
            {"last_login_at": datetime.now(timezone.utc).isoformat()}
        ).eq("email", normalize_email(email)).execute()
    except Exception:  # noqa: BLE001
        pass


def _validate_region_assignment(
    role: str,
    *,
    access_all_regions: bool,
    regions: list[str],
) -> tuple[bool, str, bool, list[str]]:
    role_norm = (role or "user").strip().lower()
    codes = normalize_region_list(regions)
    if role_norm == "user":
        if access_all_regions:
            return False, "Regular users cannot have all-region access.", False, []
        if len(codes) != 1:
            return False, "Assign exactly one region for a regular user.", False, []
        return True, "ok", False, codes
    if role_norm == "admin":
        if access_all_regions:
            return True, "ok", True, []
        if not codes:
            return (
                False,
                "Admin needs all-region access or at least one granted region.",
                False,
                [],
            )
        return True, "ok", False, codes
    return False, "Role must be user or admin.", False, []


def email_is_allowed(email: str) -> bool:
    value = normalize_email(email)
    if not is_valid_email(value):
        return False
    if auth_backend() == "supabase":
        user = _fetch_user(value)
        return bool(user and user.get("is_active"))
    return _env_email_is_allowed(value)


def current_user() -> str | None:
    email = normalize_email(st.session_state.get(SESSION_EMAIL_KEY))
    return email or None


def current_role() -> str:
    role = str(st.session_state.get(SESSION_ROLE_KEY) or "user").strip().lower()
    return role if role in {"user", "admin"} else "user"


def access_all_regions() -> bool:
    return bool(st.session_state.get(SESSION_ALL_REGIONS_KEY))


def granted_regions() -> list[str]:
    if access_all_regions():
        return list(REGION_CODES)
    raw = st.session_state.get(SESSION_REGIONS_KEY) or []
    return normalize_region_list(list(raw))


def active_region() -> str | None:
    code = normalize_region_code(st.session_state.get(SESSION_ACTIVE_REGION_KEY))
    allowed = set(granted_regions())
    if code and code in allowed:
        return code
    if allowed:
        return next(iter(allowed))
    return None


def set_active_region(code: str) -> tuple[bool, str]:
    normalized = normalize_region_code(code)
    if not normalized:
        return False, "Unknown region."
    if normalized not in set(granted_regions()):
        return False, "You do not have access to that region."
    st.session_state[SESSION_ACTIVE_REGION_KEY] = normalized
    return True, normalized


def can_access_region(code: str) -> bool:
    normalized = normalize_region_code(code)
    if not normalized:
        return False
    return normalized in set(granted_regions())


def is_admin() -> bool:
    return is_authenticated() and current_role() == "admin"


def is_authenticated() -> bool:
    email = current_user()
    if not email:
        return False
    if auth_backend() == "supabase":
        return True
    return email_is_allowed(email)


def _apply_session_from_user(
    email: str,
    *,
    role: str,
    access_all: bool,
    regions: list[str],
    first_name: str | None = None,
    last_name: str | None = None,
) -> None:
    st.session_state[SESSION_EMAIL_KEY] = email
    st.session_state[SESSION_ROLE_KEY] = role
    st.session_state[SESSION_ALL_REGIONS_KEY] = bool(access_all)
    codes = [] if access_all else normalize_region_list(regions)
    st.session_state[SESSION_REGIONS_KEY] = codes
    st.session_state[SESSION_FIRST_NAME_KEY] = _clean_name(first_name)
    st.session_state[SESSION_LAST_NAME_KEY] = _clean_name(last_name)
    allowed = list(REGION_CODES) if access_all else codes
    current = normalize_region_code(st.session_state.get(SESSION_ACTIVE_REGION_KEY))
    if current not in set(allowed):
        st.session_state[SESSION_ACTIVE_REGION_KEY] = allowed[0] if allowed else None


def login(email: str) -> tuple[bool, str]:
    value = normalize_email(email)
    if not is_valid_email(value):
        _record_login_event(value or email, success=False, detail="invalid_email")
        return False, "Enter a valid email address."

    if auth_backend() == "supabase":
        try:
            user = _fetch_user(value)
        except Exception as exc:  # noqa: BLE001
            return False, f"Could not reach user database: {exc}"
        if not user:
            _record_login_event(value, success=False, detail="unknown_user")
            return False, "This email is not registered. Ask an admin to create your account."
        if not user.get("is_active"):
            _record_login_event(value, success=False, detail="inactive_user")
            return False, "This account is inactive. Contact an admin."
        role = str(user.get("role") or "user")
        access_all = bool(user.get("access_all_regions"))
        regions = normalize_region_list(list(user.get("regions") or []))
        ok, msg, access_all, regions = _validate_region_assignment(
            role, access_all_regions=access_all, regions=regions
        )
        if not ok:
            _record_login_event(value, success=False, detail="bad_region_assignment")
            return False, f"Account region setup is invalid: {msg}"
        _apply_session_from_user(
            value,
            role=role,
            access_all=access_all,
            regions=regions,
            first_name=user.get("first_name"),
            last_name=user.get("last_name"),
        )
        _touch_last_login(value)
        _record_login_event(value, success=True, detail="ok")
        return True, value

    if not _env_email_is_allowed(value):
        return False, "This email is not authorized to use the app."
    admin_emails = {
        normalize_email(e) for e in _split_env_list(os.getenv("ALLOWED_ADMIN_EMAILS"))
    }
    admin_emails.add(normalize_email(DEFAULT_ADMIN_EMAIL))
    role = "admin" if value in admin_emails else "user"
    if role == "admin":
        _apply_session_from_user(value, role="admin", access_all=True, regions=[])
    else:
        # Env-mode users default to Malaysia unless ALLOWED_USER_REGION is set
        default_region = normalize_region_code(os.getenv("ALLOWED_USER_REGION")) or "MY"
        _apply_session_from_user(
            value, role="user", access_all=False, regions=[default_region]
        )
    return True, value


def logout() -> None:
    for key in (
        SESSION_EMAIL_KEY,
        SESSION_ROLE_KEY,
        SESSION_REGIONS_KEY,
        SESSION_ALL_REGIONS_KEY,
        SESSION_ACTIVE_REGION_KEY,
        SESSION_FIRST_NAME_KEY,
        SESSION_LAST_NAME_KEY,
    ):
        st.session_state.pop(key, None)


def list_users() -> list[dict[str, Any]]:
    client = get_supabase_client()
    result = (
        client.table("app_users")
        .select(
            "id,email,first_name,last_name,role,is_active,access_all_regions,regions,"
            "created_at,created_by,last_login_at"
        )
        .order("created_at", desc=True)
        .execute()
    )
    return list(result.data or [])


def _clean_name(value: str | None) -> str | None:
    text = re.sub(r"\s+", " ", (value or "").strip())
    return text or None


def create_user(
    email: str,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    role: str = "user",
    access_all_regions: bool = False,
    regions: list[str] | None = None,
    created_by: str | None = None,
) -> tuple[bool, str]:
    value = normalize_email(email)
    if not is_valid_email(value):
        return False, "Enter a valid email address."
    first = _clean_name(first_name)
    last = _clean_name(last_name)
    if not first:
        return False, "First name is required."
    if not last:
        return False, "Last name is required."
    ok, msg, access_all, codes = _validate_region_assignment(
        role,
        access_all_regions=access_all_regions,
        regions=regions or [],
    )
    if not ok:
        return False, msg
    client = get_supabase_client()
    if _fetch_user(value):
        return False, f"{value} is already registered."
    payload = {
        "email": value,
        "first_name": first,
        "last_name": last,
        "role": (role or "user").strip().lower(),
        "is_active": True,
        "access_all_regions": access_all,
        "regions": codes,
        "created_by": created_by or current_user(),
    }
    client.table("app_users").insert(payload).execute()
    full_name = f"{first} {last}"
    if access_all:
        return True, f"Registered {full_name} <{value}> (admin · all regions)."
    labels = ", ".join(region_label(c) for c in codes)
    return True, f"Registered {full_name} <{value}> ({payload['role']} · {labels})."


def update_user_access(
    email: str,
    *,
    role: str,
    access_all_regions: bool,
    regions: list[str] | None,
) -> tuple[bool, str]:
    value = normalize_email(email)
    ok, msg, access_all, codes = _validate_region_assignment(
        role,
        access_all_regions=access_all_regions,
        regions=regions or [],
    )
    if not ok:
        return False, msg
    client = get_supabase_client()
    client.table("app_users").update(
        {
            "role": (role or "user").strip().lower(),
            "access_all_regions": access_all,
            "regions": codes,
        }
    ).eq("email", value).execute()
    return True, f"Updated access for {value}."


def update_user_profile(
    email: str,
    *,
    first_name: str | None,
    last_name: str | None,
    role: str,
    access_all_regions: bool,
    regions: list[str] | None,
) -> tuple[bool, str]:
    value = normalize_email(email)
    first = _clean_name(first_name)
    last = _clean_name(last_name)
    if not first:
        return False, "First name is required."
    if not last:
        return False, "Last name is required."
    ok, msg, access_all, codes = _validate_region_assignment(
        role,
        access_all_regions=access_all_regions,
        regions=regions or [],
    )
    if not ok:
        return False, msg
    client = get_supabase_client()
    client.table("app_users").update(
        {
            "first_name": first,
            "last_name": last,
            "role": (role or "user").strip().lower(),
            "access_all_regions": access_all,
            "regions": codes,
        }
    ).eq("email", value).execute()
    return True, f"Updated {first} {last} <{value}>."


def set_user_active(email: str, is_active: bool) -> tuple[bool, str]:
    value = normalize_email(email)
    client = get_supabase_client()
    client.table("app_users").update({"is_active": is_active}).eq("email", value).execute()
    state = "activated" if is_active else "deactivated"
    return True, f"{value} {state}."


def list_login_events(limit: int = 100) -> list[dict[str, Any]]:
    client = get_supabase_client()
    result = (
        client.table("login_events")
        .select("id,email,logged_in_at,success,detail,user_agent")
        .order("logged_in_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(result.data or [])


def render_login_gate() -> bool:
    if is_authenticated():
        return True

    st.title("AI Door Scheduler")
    st.caption("Sign in with your work email to continue.")

    backend = auth_backend()
    if backend == "supabase":
        st.info("Accounts are managed in Supabase. Ask an admin if you need access.")
    elif not auth_is_configured():
        st.warning(
            "No email allowlist or Supabase configured. Any valid email can sign in. "
            "Set `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`, or "
            "`ALLOWED_EMAILS` / `ALLOWED_EMAIL_DOMAINS` in `.env`."
        )

    with st.form("email_login_form", clear_on_submit=False):
        email = st.text_input(
            "Email",
            placeholder="name@company.com",
            autocomplete="email",
        )
        submitted = st.form_submit_button("Continue", type="primary", use_container_width=True)

    if submitted:
        ok, detail = login(email)
        if ok:
            st.rerun()
        st.error(detail)

    st.caption("No password required — access is controlled by registered emails and regions.")
    return False


def render_sidebar_nav() -> None:
    """Admin-only Extract / Admin switch at the top of the sidebar."""
    if not is_admin():
        st.session_state["app_view"] = "extract"
        return
    st.sidebar.caption("Navigation")
    view_labels = {"extract": "Extract", "admin": "Admin"}
    current_view = st.session_state.get("app_view") or "extract"
    if current_view not in view_labels:
        current_view = "extract"
    selected = st.sidebar.radio(
        "Go to",
        options=list(view_labels.keys()),
        index=list(view_labels.keys()).index(current_view),
        format_func=lambda key: view_labels[key],
        key="sidebar_app_view",
        label_visibility="collapsed",
    )
    st.session_state["app_view"] = selected
    st.sidebar.divider()


def render_auth_sidebar() -> None:
    email = current_user()
    if not email:
        return

    st.sidebar.divider()
    st.sidebar.caption("Signed in")
    first = st.session_state.get(SESSION_FIRST_NAME_KEY) or ""
    last = st.session_state.get(SESSION_LAST_NAME_KEY) or ""
    display = f"{first} {last}".strip() or email
    st.sidebar.markdown(f"**{display}**")
    st.sidebar.caption(email)
    st.sidebar.caption(f"Role · {current_role()}")
    if auth_backend() == "supabase":
        st.sidebar.caption("Auth · Supabase")

    allowed = granted_regions()
    if access_all_regions():
        st.sidebar.caption("Regions · all")
    else:
        st.sidebar.caption("Regions · " + (", ".join(allowed) or "none"))

    if len(allowed) > 1:
        labels = {code: region_label(code) for code in allowed}
        current = active_region() or allowed[0]
        choice = st.sidebar.selectbox(
            "Active region",
            options=allowed,
            index=allowed.index(current) if current in allowed else 0,
            format_func=lambda c: labels.get(c, c),
            key="sidebar_active_region",
        )
        if choice != st.session_state.get(SESSION_ACTIVE_REGION_KEY):
            set_active_region(choice)
    elif allowed:
        st.sidebar.caption(f"Active · {region_label(allowed[0])}")
        st.session_state[SESSION_ACTIVE_REGION_KEY] = allowed[0]

    if st.sidebar.button("Log out", use_container_width=True):
        logout()
        st.rerun()


def _format_user_regions(user: dict[str, Any]) -> str:
    if user.get("access_all_regions"):
        return "All regions"
    codes = normalize_region_list(list(user.get("regions") or []))
    if not codes:
        return "—"
    return ", ".join(region_label(c) for c in codes)


def render_admin_panel() -> None:
    st.subheader("Admin · users, regions & login activity")
    if auth_backend() != "supabase":
        st.warning(
            "Admin tools require Supabase. Set `SUPABASE_URL` and "
            "`SUPABASE_SERVICE_ROLE_KEY`, run `supabase/schema.sql`, then sign in as admin."
        )
        return
    if not is_admin():
        st.error("Admin access required.")
        return

    tab_users, tab_logins, tab_regions = st.tabs(
        ["Users", "Login activity", "Regions"]
    )

    with tab_regions:
        st.markdown("#### Available regions")
        st.dataframe(
            [{"Code": code, "Region": name} for code, name in REGIONS.items()],
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "Users are assigned **one** region. Admins get **all regions** or a "
            "granted subset."
        )

    with tab_users:
        st.markdown("#### Register user")
        with st.form("admin_register_user", clear_on_submit=True):
            new_email = st.text_input("Email", placeholder="name@company.com")
            name_cols = st.columns(2)
            with name_cols[0]:
                first_name = st.text_input("First name", placeholder="First name")
            with name_cols[1]:
                last_name = st.text_input("Last name", placeholder="Last name")
            region = st.selectbox(
                "Region",
                options=list(REGION_CODES),
                format_func=lambda c: region_label(c),
                index=list(REGION_CODES).index("MY"),
            )
            create_submit = st.form_submit_button(
                "Register user", type="primary", use_container_width=True
            )
        if create_submit:
            ok, msg = create_user(
                new_email,
                first_name=first_name,
                last_name=last_name,
                role="user",
                access_all_regions=False,
                regions=[region],
                created_by=current_user(),
            )
            if ok:
                st.success(msg)
            else:
                st.error(msg)
        st.caption(
            "New registrations are created as **user** with one region. "
            "Promote to admin or change region in **Edit access** below."
        )

        st.markdown("#### Registered users")
        try:
            users = list_users()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not load users: {exc}")
            users = []

        if not users:
            st.info(
                "No users yet. Run `supabase/schema.sql` to seed "
                f"`{DEFAULT_ADMIN_EMAIL}`, or register a user above."
            )
        else:
            for user in users:
                email = user.get("email", "")
                full_name = " ".join(
                    p
                    for p in [
                        (user.get("first_name") or "").strip(),
                        (user.get("last_name") or "").strip(),
                    ]
                    if p
                ) or "—"
                st.markdown("---")
                top = st.columns([1.6, 2.0, 0.7, 0.7, 2.0, 1.0])
                top[0].markdown(f"**{full_name}**")
                top[1].write(email)
                top[2].write(user.get("role", "user"))
                top[3].write("Active" if user.get("is_active") else "Inactive")
                top[4].caption(_format_user_regions(user))
                last = user.get("last_login_at") or "—"
                top[5].caption(str(last)[:19].replace("T", " "))

                with st.expander(f"Edit access · {email}", expanded=False):
                    name_edit = st.columns(2)
                    with name_edit[0]:
                        edit_first = st.text_input(
                            "First name",
                            value=user.get("first_name") or "",
                            key=f"fn_{email}",
                        )
                    with name_edit[1]:
                        edit_last = st.text_input(
                            "Last name",
                            value=user.get("last_name") or "",
                            key=f"ln_{email}",
                        )
                    role_val = st.selectbox(
                        "Role",
                        options=["user", "admin"],
                        index=0 if user.get("role") == "user" else 1,
                        key=f"role_{email}",
                    )
                    if role_val == "admin":
                        all_regions = st.checkbox(
                            "Access all regions",
                            value=bool(user.get("access_all_regions")),
                            key=f"all_{email}",
                        )
                        granted = st.multiselect(
                            "Granted regions (if not all)",
                            options=list(REGION_CODES),
                            default=normalize_region_list(
                                list(user.get("regions") or [])
                            ),
                            format_func=lambda c: region_label(c),
                            key=f"regs_{email}",
                        )
                    else:
                        all_regions = False
                        current_one = normalize_region_list(
                            list(user.get("regions") or [])
                        )
                        default_code = current_one[0] if current_one else "MY"
                        one = st.selectbox(
                            "Region",
                            options=list(REGION_CODES),
                            index=list(REGION_CODES).index(default_code)
                            if default_code in REGION_CODES
                            else 0,
                            format_func=lambda c: region_label(c),
                            key=f"one_{email}",
                        )
                        granted = [one]

                    b1, b2, _b3 = st.columns(3)
                    with b1:
                        if st.button("Save", key=f"save_{email}"):
                            ok, msg = update_user_profile(
                                email,
                                first_name=edit_first,
                                last_name=edit_last,
                                role=role_val,
                                access_all_regions=all_regions,
                                regions=granted,
                            )
                            if ok:
                                st.success(msg)
                                st.rerun()
                            st.error(msg)
                    with b2:
                        if user.get("is_active"):
                            if st.button("Deactivate", key=f"deact_{email}"):
                                ok, msg = set_user_active(email, False)
                                st.toast(msg)
                                st.rerun()
                        else:
                            if st.button("Activate", key=f"act_{email}"):
                                ok, msg = set_user_active(email, True)
                                st.toast(msg)
                                st.rerun()

    with tab_logins:
        st.markdown("#### Recent logins")
        try:
            events = list_login_events(limit=150)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not load login events: {exc}")
            events = []
        if not events:
            st.info("No login events recorded yet.")
        else:
            st.dataframe(events, use_container_width=True, hide_index=True)
