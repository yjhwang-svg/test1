"""
마케팅 일별 리포트 대시보드 (SQLite + Streamlit)
실행: streamlit run app.py
DB 없을 시: python setup_data.py
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "marketing.db"

ADMIN_USER = "admin"
ADMIN_PASSWORD_SHA256 = hashlib.sha256("admin1234".encode("utf-8")).hexdigest()

MAX_FAILED_ATTEMPTS = 3
LOCKOUT_MINUTES = 5


def _password_ok(plain: str) -> bool:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest() == ADMIN_PASSWORD_SHA256


def _init_auth_state() -> None:
    if "auth_ok" not in st.session_state:
        st.session_state.auth_ok = False
    if "fail_count" not in st.session_state:
        st.session_state.fail_count = 0
    if "lockout_until" not in st.session_state:
        st.session_state.lockout_until = None


def _now() -> datetime:
    return datetime.now()


def _clear_lockout_if_expired() -> None:
    lu = st.session_state.lockout_until
    if lu is not None and _now() >= lu:
        st.session_state.lockout_until = None
        st.session_state.fail_count = 0


def _is_locked() -> bool:
    _clear_lockout_if_expired()
    lu = st.session_state.lockout_until
    return lu is not None and _now() < lu


def _lockout_remaining_text() -> str:
    lu = st.session_state.lockout_until
    if lu is None:
        return ""
    sec = max(0, int((lu - _now()).total_seconds()))
    m, s = divmod(sec, 60)
    return f"{m}분 {s}초"


def ensure_db() -> None:
    """Git/Cloud 배포 시 marketing.db가 없으면 스크립트와 동일 경로에 생성."""
    if DB_PATH.is_file():
        return
    from setup_data import create_db

    create_db()


def load_report() -> pd.DataFrame:
    if not DB_PATH.is_file():
        return pd.DataFrame()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        df = pd.read_sql("SELECT * FROM daily_report", conn)
    finally:
        conn.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df


def render_login() -> None:
    st.title("마케팅 대시보드")
    st.caption("로그인이 필요합니다.")

    if _is_locked():
        st.error(f"로그인 시도가 {MAX_FAILED_ATTEMPTS}회 초과되어 {_lockout_remaining_text()} 후에 다시 시도할 수 있습니다.")
        return

    with st.form("login_form"):
        uid = st.text_input("아이디", autocomplete="username")
        pwd = st.text_input("비밀번호", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("로그인")

    if not submitted:
        return

    if uid.strip() != ADMIN_USER or not _password_ok(pwd):
        st.session_state.fail_count += 1
        left = MAX_FAILED_ATTEMPTS - st.session_state.fail_count
        if st.session_state.fail_count >= MAX_FAILED_ATTEMPTS:
            st.session_state.lockout_until = _now() + timedelta(minutes=LOCKOUT_MINUTES)
            st.session_state.fail_count = 0
            st.error(
                f"인증에 실패했습니다. {LOCKOUT_MINUTES}분간 로그인이 제한됩니다."
            )
            st.rerun()
        st.error(f"아이디 또는 비밀번호가 올바르지 않습니다. ({left}회 남음)")
        return

    st.session_state.auth_ok = True
    st.session_state.fail_count = 0
    st.session_state.lockout_until = None
    st.rerun()


def render_dashboard(df: pd.DataFrame) -> None:
    st.title("마케팅 성과 대시보드")
    st.caption("`daily_report` 기준 · 필터는 사이드바에서 조정합니다.")

    if df.empty:
        st.warning("데이터가 없습니다. 터미널에서 `python setup_data.py`로 DB를 생성하세요.")
        return

    dmin, dmax = df["date"].min().date(), df["date"].max().date()
    channels = sorted(df["channel"].unique().tolist())
    campaigns = sorted(df["campaign"].unique().tolist())

    with st.sidebar:
        st.header("필터")
        dr = st.date_input(
            "기간",
            value=(dmin, dmax),
            min_value=dmin,
            max_value=dmax,
        )
        if isinstance(dr, tuple) and len(dr) == 2:
            start_d, end_d = dr
        elif hasattr(dr, "year"):
            start_d = end_d = dr
        else:
            start_d, end_d = dmin, dmax

        sel_ch = st.multiselect("채널", options=channels, default=channels)
        sel_ca = st.multiselect("캠페인", options=campaigns, default=campaigns)

        st.divider()
        if st.button("로그아웃"):
            st.session_state.auth_ok = False
            st.rerun()

    mask = (
        (df["date"].dt.date >= start_d)
        & (df["date"].dt.date <= end_d)
        & (df["channel"].isin(sel_ch))
        & (df["campaign"].isin(sel_ca))
    )
    f = df.loc[mask].copy()
    if f.empty:
        st.info("선택한 조건에 맞는 데이터가 없습니다.")
        return

    total_cost = f["cost"].sum()
    total_rev = f["revenue"].sum()
    total_conv = f["conversions"].sum()
    total_clicks = f["clicks"].sum()
    total_imp = f["impressions"].sum()
    roas = (total_rev / total_cost) if total_cost else 0.0
    ctr = (total_clicks / total_imp * 100) if total_imp else 0.0
    cpc = (total_cost / total_clicks) if total_clicks else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총 비용", f"{total_cost:,.0f}원")
    c2.metric("총 매출", f"{total_rev:,.0f}원")
    c3.metric("ROAS", f"{roas:.2f}")
    c4.metric("총 전환", f"{total_conv:,.0f}")

    c5, c6 = st.columns(2)
    c5.metric("CTR", f"{ctr:.2f}%")
    c6.metric("평균 CPC", f"{cpc:,.0f}원")

    daily = (
        f.assign(일자=f["date"].dt.date)
        .groupby("일자", as_index=False)
        .agg(cost=("cost", "sum"), revenue=("revenue", "sum"))
        .sort_values("일자")
    )

    st.subheader("일별 비용 · 매출")
    st.line_chart(daily.set_index("일자")[["cost", "revenue"]], height=320)

    by_ch = f.groupby("channel", as_index=False).agg(
        cost=("cost", "sum"),
        revenue=("revenue", "sum"),
        conversions=("conversions", "sum"),
    )
    c = by_ch["cost"].replace(0, pd.NA)
    by_ch["roas"] = (by_ch["revenue"] / c).fillna(0.0)
    by_ch = by_ch.sort_values("cost", ascending=False)

    st.subheader("채널별 요약")
    st.dataframe(
        by_ch,
        width="stretch",
        hide_index=True,
        column_config={
            "channel": st.column_config.TextColumn("채널"),
            "cost": st.column_config.NumberColumn("비용", format="%d"),
            "revenue": st.column_config.NumberColumn("매출", format="%d"),
            "conversions": st.column_config.NumberColumn("전환", format="%d"),
            "roas": st.column_config.NumberColumn("ROAS", format="%.2f"),
        },
    )

    chart_data = by_ch.set_index("channel")[["cost", "revenue"]]
    st.bar_chart(chart_data, height=360)


def main() -> None:
    st.set_page_config(
        page_title="마케팅 대시보드",
        page_icon=":bar_chart:",
        layout="wide",
    )

    ensure_db()
    _init_auth_state()

    if not st.session_state.auth_ok:
        render_login()
        return

    df = load_report()
    render_dashboard(df)


if __name__ == "__main__":
    main()
