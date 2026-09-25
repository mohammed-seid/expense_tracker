import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import hmac
import os
import json
import base64
import io
import shutil
import calendar
import re
import requests
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

# ============================================================================
# CONFIGURATION & CONSTANTS
# ============================================================================

st.set_page_config(
    page_title="Ledgerly | Smart Expense Tracker",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DATA_FILE = Path(__file__).with_name("expenses.csv")
BACKUP_FILE = Path(__file__).with_name("expenses.backup.csv")
SETTINGS_FILE = Path(__file__).with_name("user_settings.json")

TRANSACTION_COLUMNS = [
    "id", "date", "type", "amount", "category", "detail",
    "payment_method", "notes", "created_at",
]

INCOME_SOURCES = ["Salary", "Investment", "Business", "Freelance", "Gift", "Other"]
EXPENSE_RECIPIENTS = ["Self", "Wife / husband", "Family", "Kids", "Friends", "Shared household", "Other"]

EXPENSE_CATEGORIES = [
    "Food & dining", "Transport", "Clothes & shoes", "Utilities",
    "Rent / housing", "Health", "Education", "Entertainment",
    "Shopping", "Debt payment", "Gifts", "Other",
]

CATEGORY_ICONS: Dict[str, str] = {
    "Food & dining": "🍔",
    "Transport": "🚗",
    "Clothes & shoes": "👕",
    "Utilities": "💡",
    "Rent / housing": "🏠",
    "Health": "💊",
    "Education": "📚",
    "Entertainment": "🎬",
    "Shopping": "🛍️",
    "Debt payment": "💳",
    "Gifts": "🎁",
    "Other": "📦",
    "Income": "💰",
    "Salary": "💼",
    "Investment": "📈",
    "Business": "🏢",
    "Freelance": "💻",
    "Gift": "🎀",
}

PAYMENT_METHODS = ["Cash", "Mobile money", "Debit card", "Credit card", "Bank transfer", "Other"]
PAYMENT_ICONS: Dict[str, str] = {
    "Cash": "💵",
    "Mobile money": "📱",
    "Debit card": "💳",
    "Credit card": "💳",
    "Bank transfer": "🏦",
    "Other": "🔄",
}

CURRENCIES = ["ETB", "USD"]
CURRENCY_SYMBOLS = {"ETB": "Br", "USD": "$"}
DEFAULT_USD_TO_ETB_RATE = 130.0


# ============================================================================
# SETTINGS & PREFERENCES LAYER
# ============================================================================

def load_user_settings() -> Dict[str, Any]:
    default_settings = {
        "monthly_budget_usd": 1000.0,
        "display_currency": "ETB",
        "exchange_rate_usd_to_etb": DEFAULT_USD_TO_ETB_RATE,
        "monthly_budget": 1000.0,
        "currency": "ETB",
        "nav_position": "Bottom (Mobile)",
        "category_budgets": {},
    }
    if not SETTINGS_FILE.exists():
        return default_settings
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            settings = {**default_settings, **data}
            if "monthly_budget_usd" not in data:
                settings["monthly_budget_usd"] = 1000.0
            if "display_currency" not in data:
                settings["display_currency"] = "ETB"
            if "nav_position" not in data:
                settings["nav_position"] = "Bottom (Mobile)"
            if "category_budgets" not in data:
                settings["category_budgets"] = {}
            settings["monthly_budget"] = settings.get("monthly_budget", settings["monthly_budget_usd"])
            settings["currency"] = settings.get("currency", settings["display_currency"])
            return settings
    except Exception:
        return default_settings


def save_user_settings(settings: Dict[str, Any]) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        st.warning(f"Could not save settings: {e}")


# ============================================================================
# DATA LAYER (Zero-Data-Loss Guaranteed)
# ============================================================================

def github_storage_config() -> Optional[Dict[str, str]]:
    """Returns GitHub storage settings when configured in Streamlit secrets."""
    try:
        settings = st.secrets["github"]
    except (KeyError, FileNotFoundError):
        return None

    config = {
        "token": str(settings.get("token", "")).strip(),
        "owner": str(settings.get("owner", "")).strip(),
        "repo": str(settings.get("repo", "")).strip(),
        "path": str(settings.get("csv_path", "expenses.csv")).strip(),
    }
    if not all(config[key] for key in ("token", "owner", "repo", "path")):
        raise RuntimeError("GitHub storage is configured, but token, owner, repo, or csv_path is missing.")
    branch = str(settings.get("branch", "")).strip()
    if branch:
        config["branch"] = branch
    return config


def github_file_url(config: Dict[str, str]) -> str:
    path = requests.utils.quote(config["path"], safe="/")
    return f"https://api.github.com/repos/{config['owner']}/{config['repo']}/contents/{path}"


def github_headers(config: Dict[str, str]) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {config['token']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def read_github_transactions(config: Dict[str, str]) -> Tuple[pd.DataFrame, Optional[str]]:
    params = {"ref": config["branch"]} if config.get("branch") else None
    try:
        response = requests.get(
            github_file_url(config), headers=github_headers(config), params=params, timeout=15
        )
        if response.status_code == 404:
            return pd.DataFrame(columns=TRANSACTION_COLUMNS), None
        response.raise_for_status()
        file_info = response.json()
        csv_bytes = base64.b64decode(file_info["content"])
        transactions = pd.read_csv(io.BytesIO(csv_bytes), dtype={"id": str})
    except (requests.RequestException, KeyError, ValueError, pd.errors.ParserError) as exc:
        raise RuntimeError("Could not read transactions from the configured GitHub repository.") from exc

    for column in TRANSACTION_COLUMNS:
        if column not in transactions:
            transactions[column] = ""
    transactions["date"] = pd.to_datetime(transactions["date"], errors="coerce").dt.date
    transactions["amount"] = pd.to_numeric(transactions["amount"], errors="coerce").fillna(0.0)
    transactions["notes"] = transactions["notes"].fillna("").astype(str)
    transactions["id"] = transactions["id"].astype(str)
    transactions = transactions[TRANSACTION_COLUMNS]
    transactions.attrs["github_sha"] = file_info.get("sha")
    return transactions, file_info.get("sha")


def write_github_transactions(transactions: pd.DataFrame, config: Dict[str, str]) -> None:
    _, current_sha = read_github_transactions(config)
    expected_sha = transactions.attrs.get("github_sha")
    if expected_sha != current_sha:
        raise RuntimeError("The GitHub CSV changed since it was loaded. Reload the app and retry your change.")

    df_to_save = transactions.copy()
    df_to_save["date"] = pd.to_datetime(df_to_save["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    content = base64.b64encode(df_to_save.to_csv(index=False).encode("utf-8")).decode("ascii")
    payload: Dict[str, str] = {
        "message": "Update expense transactions",
        "content": content,
    }
    if current_sha:
        payload["sha"] = current_sha
    if config.get("branch"):
        payload["branch"] = config["branch"]

    try:
        response = requests.put(
            github_file_url(config), headers=github_headers(config), json=payload, timeout=15
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError("Could not save transactions to GitHub; the existing remote CSV was preserved.") from exc


def backup_data() -> None:
    """Creates a local backup before modifying existing data."""
    try:
        if DATA_FILE.exists():
            shutil.copy2(DATA_FILE, BACKUP_FILE)
    except Exception:
        pass


def load_transactions() -> pd.DataFrame:
    """Safely loads transactions while preserving column integrity."""
    config = github_storage_config()
    if config:
        transactions, _ = read_github_transactions(config)
        return transactions

    if not DATA_FILE.exists():
        return pd.DataFrame(columns=TRANSACTION_COLUMNS)
    try:
        transactions = pd.read_csv(DATA_FILE, dtype={"id": str})
    except (OSError, pd.errors.ParserError) as exc:
        raise RuntimeError(f"Could not read {DATA_FILE.name}; no changes were saved.") from exc

    for column in TRANSACTION_COLUMNS:
        if column not in transactions:
            transactions[column] = ""

    transactions["date"] = pd.to_datetime(transactions["date"], errors="coerce").dt.date
    transactions["amount"] = pd.to_numeric(transactions["amount"], errors="coerce").fillna(0.0)
    transactions["notes"] = transactions["notes"].fillna("").astype(str)
    transactions["id"] = transactions["id"].astype(str)
    return transactions[TRANSACTION_COLUMNS]


def save_transactions(transactions: pd.DataFrame) -> None:
    """Safely writes transactions to disk with prior backup."""
    config = github_storage_config()
    if config:
        write_github_transactions(transactions, config)
        return

    backup_data()
    df_to_save = transactions.copy()
    df_to_save["date"] = pd.to_datetime(df_to_save["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    temporary_file = DATA_FILE.with_suffix(DATA_FILE.suffix + ".tmp")
    try:
        df_to_save.to_csv(temporary_file, index=False)
        os.replace(temporary_file, DATA_FILE)
    except OSError as exc:
        if temporary_file.exists():
            temporary_file.unlink()
        raise RuntimeError(f"Could not save {DATA_FILE.name}; existing data was preserved.") from exc


def add_transaction(
    transaction_type: str,
    amount: float,
    transaction_date: date,
    category: str,
    detail: str,
    payment_method: str,
    notes: str,
) -> str:
    """Adds a new transaction record."""
    transactions = load_transactions()
    new_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    record = pd.DataFrame([{
        "id": new_id,
        "date": transaction_date,
        "type": transaction_type,
        "amount": round(float(amount), 2),
        "category": category,
        "detail": detail,
        "payment_method": payment_method,
        "notes": notes.strip(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }])
    record.attrs.update(transactions.attrs)
    if transactions.empty:
        save_transactions(record)
    else:
        save_transactions(pd.concat([transactions, record], ignore_index=True))
    return new_id


def update_transaction(
    transaction_id: str,
    transaction_type: str,
    amount: float,
    transaction_date: date,
    category: str,
    detail: str,
    payment_method: str,
    notes: str,
) -> bool:
    """Updates an existing transaction record by ID."""
    transactions = load_transactions()
    idx = transactions.index[transactions["id"] == str(transaction_id)].tolist()
    if not idx:
        return False
    row_idx = idx[0]
    transactions.at[row_idx, "type"] = transaction_type
    transactions.at[row_idx, "amount"] = round(float(amount), 2)
    transactions.at[row_idx, "date"] = transaction_date
    transactions.at[row_idx, "category"] = category
    transactions.at[row_idx, "detail"] = detail
    transactions.at[row_idx, "payment_method"] = payment_method
    transactions.at[row_idx, "notes"] = notes.strip()
    save_transactions(transactions)
    return True


def delete_transaction(transaction_id: str) -> bool:
    """Deletes a transaction record safely."""
    transactions = load_transactions()
    initial_count = len(transactions)
    transactions = transactions[transactions["id"] != str(transaction_id)]
    if len(transactions) < initial_count:
        save_transactions(transactions)
        return True
    return False


# ============================================================================
# DATE & FILTER HELPERS
# ============================================================================

def get_date_preset_range(preset: str, today: Optional[date] = None) -> Optional[Tuple[date, date]]:
    """Returns (start_date, end_date) for named date presets."""
    if today is None:
        today = date.today()

    if preset == "This Month":
        start = today.replace(day=1)
        _, last_day = calendar.monthrange(today.year, today.month)
        end = today.replace(day=last_day)
        return start, end
    elif preset == "Last Month":
        first_of_this_month = today.replace(day=1)
        last_of_prev_month = first_of_this_month - timedelta(days=1)
        start_of_prev_month = last_of_prev_month.replace(day=1)
        return start_of_prev_month, last_of_prev_month
    elif preset == "Last 30 Days":
        return today - timedelta(days=30), today
    elif preset == "This Year":
        return date(today.year, 1, 1), date(today.year, 12, 31)
    elif preset == "This Week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return start, end
    elif preset == "All Time":
        return None
    return None


def filtered_transactions(
    transactions: pd.DataFrame,
    date_range: Optional[Tuple[date, date]] = None,
    tx_type: Optional[str] = None,
    category: Optional[str] = None,
    search_term: Optional[str] = None,
) -> pd.DataFrame:
    """Filters the transaction dataframe across date, type, category, and keyword search."""
    if transactions.empty:
        return transactions

    filtered = transactions.copy()

    if date_range and len(date_range) == 2 and date_range[0] and date_range[1]:
        start, end = date_range
        filtered = filtered[filtered["date"].between(start, end)]

    if tx_type and tx_type != "All":
        filtered = filtered[filtered["type"] == tx_type]

    if category and category != "All":
        filtered = filtered[filtered["category"] == category]

    if search_term and search_term.strip():
        term = search_term.strip().lower()
        search_blob = (
            filtered["category"].astype(str) + " " +
            filtered["detail"].astype(str) + " " +
            filtered["payment_method"].astype(str) + " " +
            filtered["notes"].astype(str) + " " +
            filtered["amount"].astype(str)
        ).str.lower()
        filtered = filtered[search_blob.str.contains(term, regex=False)]

    return filtered


def format_currency(amount: float, currency_symbol: str = "$") -> str:
    """Formats float amount to readable currency."""
    sym = CURRENCY_SYMBOLS.get(currency_symbol, currency_symbol)
    spacing = " " if len(sym) > 1 else ""
    return f"{sym}{spacing}{amount:,.2f}"


def convert_amount(amount: float, display_currency: str, usd_to_etb_rate: float) -> float:
    """Converts an ETB-stored amount into the selected display currency."""
    if display_currency == "USD":
        return float(amount) / usd_to_etb_rate if usd_to_etb_rate > 0 else 0.0
    return float(amount)


def convert_usd_budget(amount: float, display_currency: str, usd_to_etb_rate: float) -> float:
    """Converts a USD budget into the selected display currency."""
    return float(amount) * usd_to_etb_rate if display_currency == "ETB" else float(amount)


def display_transactions(
    transactions: pd.DataFrame,
    display_currency: str,
    usd_to_etb_rate: float,
) -> pd.DataFrame:
    """Returns a display-only copy; the CSV remains stored in ETB."""
    displayed = transactions.copy()
    if not displayed.empty:
        displayed["amount"] = displayed["amount"].apply(
            lambda amount: convert_amount(amount, display_currency, usd_to_etb_rate)
        )
    return displayed


# Caching live rate scraping to prevent blocking synchronous HTTP delays on every rerun
@st.cache_data(ttl=3600, show_spinner=False)
def _scrape_live_rate() -> Optional[float]:
    """Scrapes Google Finance for USD/ETB quote with a strict 3-second timeout."""
    try:
        response = requests.get(
            "https://www.google.com/finance/quote/USD-ETB",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=3,
        )
        if response.status_code == 200:
            page = response.text
            matches = re.findall(
                r'(?:data-last-price|data-price|price)\s*[:=]\s*["\']?([0-9]+(?:\.[0-9]+)?)',
                page,
                flags=re.IGNORECASE,
            )
            if not matches:
                matches = re.findall(r'0,0,([0-9]+(?:\.[0-9]+)?),4,1', page)
            rate = next(
                (float(value) for value in matches if 50.0 < float(value) < 500.0),
                0.0,
            )
            if rate > 0:
                return rate
    except Exception:
        pass
    return None


def fetch_usd_to_etb_rate(settings: Dict[str, Any], force_refresh: bool = False) -> Tuple[float, bool]:
    """Fetches the USD/ETB quote from Google Finance with caching, falling back to stored rate."""
    cached_rate = float(settings.get("exchange_rate_usd_to_etb", DEFAULT_USD_TO_ETB_RATE))
    if force_refresh:
        _scrape_live_rate.clear()
    live_rate = _scrape_live_rate()
    if live_rate is not None and live_rate > 0:
        if abs(live_rate - cached_rate) > 0.001:
            settings["exchange_rate_usd_to_etb"] = live_rate
            settings["exchange_rate_updated_at"] = datetime.now().isoformat(timespec="seconds")
            save_user_settings(settings)
        return live_rate, True
    return cached_rate, False


def get_category_icon(category_name: str) -> str:
    """Retrieves emoji icon for category."""
    return CATEGORY_ICONS.get(category_name, "🏷️")


def get_payment_icon(payment_method: str) -> str:
    """Retrieves emoji icon for payment method."""
    return PAYMENT_ICONS.get(payment_method, "💳")


# ============================================================================
# AUTHENTICATION
# ============================================================================

def configured_password() -> Optional[str]:
    try:
        password = st.secrets.get("LEDGERLY_PASSWORD")
        if not password:
            auth_config = st.secrets.get("auth", {})
            password = auth_config.get("password") if hasattr(auth_config, "get") else None
    except (FileNotFoundError, KeyError, TypeError):
        password = None
    return password or os.getenv("LEDGERLY_PASSWORD")


def require_authentication() -> bool:
    expected_password = configured_password()
    if not expected_password:
        return True

    if st.session_state.get("authenticated", False):
        return True

    st.markdown(
        """
        <div style="text-align: center; margin: 4rem auto 1.5rem; max-width: 420px;">
            <div style="font-size: 3.5rem; margin-bottom: 0.5rem; filter: drop-shadow(0 4px 10px rgba(0,0,0,0.1));">💳</div>
            <h1 style="font-size: 2rem; font-weight: 800; margin-bottom: 0.3rem; letter-spacing: -0.03em;">Ledgerly</h1>
            <p style="color: #64748b; font-size: 0.95rem;">Enter your passcode to unlock your personal finances.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        with st.form("login_form"):
            password = st.text_input("Passcode", type="password", placeholder="Enter password")
            submitted = st.form_submit_button("Unlock Ledger", type="primary", use_container_width=True)
            if submitted:
                if hmac.compare_digest(password, expected_password):
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("Incorrect password. Please try again.", icon="🚨")

    st.caption("<div style='text-align:center; color:#94a3b8; font-size:0.8rem;'>Your ledger is encrypted and kept private on this device.</div>", unsafe_allow_html=True)
    return False


# ============================================================================
# MODERN DESIGN SYSTEM & MOBILE CSS
# ============================================================================

def inject_mobile_css(nav_position: str = "Bottom (Mobile)") -> None:
    is_bottom = "Bottom" in str(nav_position)

    if is_bottom:
        nav_styles = """
        /* BOTTOM NAVIGATION DOCK (Mobile First & Modern) */
        div.st-key-main_nav_radio [data-testid="stRadio"] {
            position: fixed !important;
            bottom: 0 !important;
            left: 0 !important;
            right: 0 !important;
            width: 100vw !important;
            z-index: 999999 !important;
            background: rgba(255, 255, 255, 0.95) !important;
            backdrop-filter: blur(14px) !important;
            -webkit-backdrop-filter: blur(14px) !important;
            border-top: 1px solid rgba(226, 232, 240, 0.9) !important;
            border-bottom: none !important;
            padding: 0.5rem 0.6rem calc(0.5rem + env(safe-area-inset-bottom, 0px)) !important;
            box-shadow: 0 -4px 24px rgba(15, 23, 42, 0.08) !important;
            margin: 0 !important;
        }

        div.st-key-main_nav_radio [data-testid="stRadio"] > div {
            display: flex !important;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            justify-content: space-around !important;
            align-items: center !important;
            max-width: 680px !important;
            margin: 0 auto !important;
            gap: 0.4rem !important;
            overflow-x: auto !important;
            scrollbar-width: none !important;
        }

        div.st-key-main_nav_radio [data-testid="stRadio"] label {
            flex: 1 1 0 !important;
            min-height: 44px !important;
            padding: 0.45rem 0.25rem !important;
        }

        div.st-key-main_nav_radio [data-testid="stRadio"] label p {
            font-size: 0.8rem !important;
        }
        """
        container_padding_top = "3.2rem"
        container_padding_bottom = "6.5rem"
    else:
        nav_styles = """
        /* TOP NAVIGATION HEADER (Desktop & Tablet) */
        div.st-key-main_nav_radio [data-testid="stRadio"] {
            position: sticky !important;
            top: 3.2rem !important;
            z-index: 999 !important;
            background: rgba(248, 250, 252, 0.96) !important;
            backdrop-filter: blur(12px) !important;
            -webkit-backdrop-filter: blur(12px) !important;
            border-bottom: 1px solid var(--line) !important;
            padding: 0.4rem 0 0.65rem 0 !important;
            margin-bottom: 1rem !important;
        }

        div.st-key-main_nav_radio [data-testid="stRadio"] > div {
            display: flex !important;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
            gap: 0.6rem !important;
            overflow-x: auto !important;
            padding: 0.2rem 0 !important;
            scrollbar-width: none !important;
            max-width: 900px !important;
            margin: 0 auto !important;
        }

        div.st-key-main_nav_radio [data-testid="stRadio"] label {
            flex: 0 0 auto !important;
            min-height: 42px !important;
            padding: 0.55rem 1.1rem !important;
        }

        div.st-key-main_nav_radio [data-testid="stRadio"] label p {
            font-size: 0.88rem !important;
        }
        """
        container_padding_top = "4.2rem"
        container_padding_bottom = "4rem"

    st.markdown(
        f"""
        <style>
        :root {{
            --ink: #0f172a;
            --ink-secondary: #334155;
            --muted: #64748b;
            --line: #e2e8f0;
            --surface: #ffffff;
            --surface-subtle: #f8fafc;
            --primary: #059669;
            --primary-hover: #047857;
            --primary-light: #ecfdf5;
            --danger: #dc2626;
            --danger-light: #fef2f2;
            --success: #16a34a;
            --success-light: #f0fdf4;
            --warning: #d97706;
            --warning-light: #fef3c7;
            --card-shadow: 0 4px 16px -2px rgba(15, 23, 42, 0.05), 0 2px 6px -1px rgba(15, 23, 42, 0.02);
            --card-shadow-hover: 0 12px 28px -4px rgba(15, 23, 42, 0.09), 0 4px 10px -2px rgba(15, 23, 42, 0.03);
        }}

        /* App Background & Typography */
        .stApp {{
            background: linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%) !important;
            color: var(--ink) !important;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Inter", sans-serif !important;
        }}

        h1, h2, h3, h4 {{
            color: var(--ink) !important;
            letter-spacing: -0.025em !important;
            font-weight: 700 !important;
        }}

        /* Container Spacing */
        [data-testid="stMainBlockContainer"] {{
            padding-top: {container_padding_top} !important;
            padding-bottom: {container_padding_bottom} !important;
            max-width: 920px !important;
            margin: 0 auto;
        }}

        /* Modern Glass Header */
        header[data-testid="stHeader"] {{
            background: rgba(248, 250, 252, 0.88) !important;
            backdrop-filter: blur(10px) !important;
            -webkit-backdrop-filter: blur(10px) !important;
            height: 3.2rem !important;
            z-index: 99 !important;
            border-bottom: 1px solid rgba(226, 232, 240, 0.6) !important;
        }}

        /* Modernized Metric Cards */
        [data-testid="stMetric"] {{
            background: #ffffff !important;
            border: 1px solid var(--line) !important;
            border-radius: 16px !important;
            padding: 0.95rem 1.15rem !important;
            box-shadow: var(--card-shadow) !important;
            position: relative !important;
            overflow: hidden !important;
            transition: transform 0.16s ease, box-shadow 0.16s ease, border-color 0.16s ease !important;
        }}

        [data-testid="stMetric"]::before {{
            content: "";
            position: absolute;
            top: 0;
            left: 0;
            width: 4px;
            height: 100%;
            background: linear-gradient(180deg, #10b981, #059669);
            border-radius: 4px 0 0 4px;
        }}

        [data-testid="stMetric"]:hover {{
            transform: translateY(-2px) !important;
            border-color: #cbd5e1 !important;
            box-shadow: var(--card-shadow-hover) !important;
        }}

        [data-testid="stMetricLabel"] p {{
            font-size: 0.78rem !important;
            color: var(--muted) !important;
            font-weight: 700 !important;
            text-transform: uppercase !important;
            letter-spacing: 0.06em !important;
        }}

        [data-testid="stMetricValue"] {{
            font-weight: 800 !important;
            font-size: 1.55rem !important;
            color: var(--ink) !important;
            letter-spacing: -0.02em !important;
        }}

        [data-testid="stMetricDelta"] {{
            font-weight: 600 !important;
            font-size: 0.82rem !important;
        }}

        /* Interactive Buttons */
        button[kind="primary"], .stButton > button[kind="primary"] {{
            background: linear-gradient(180deg, #059669 0%, #047857 100%) !important;
            color: #ffffff !important;
            border: none !important;
            border-radius: 12px !important;
            min-height: 46px !important;
            font-weight: 600 !important;
            box-shadow: 0 4px 14px rgba(5, 150, 105, 0.25) !important;
            transition: all 0.16s ease !important;
        }}

        button[kind="primary"]:hover, .stButton > button[kind="primary"]:hover {{
            background: linear-gradient(180deg, #047857 0%, #065f46 100%) !important;
            box-shadow: 0 6px 20px rgba(5, 150, 105, 0.35) !important;
            transform: translateY(-1px) !important;
        }}

        button[kind="secondary"], .stButton > button[kind="secondary"] {{
            background: #ffffff !important;
            color: var(--ink) !important;
            border: 1px solid var(--line) !important;
            border-radius: 12px !important;
            min-height: 44px !important;
            font-weight: 600 !important;
            transition: all 0.16s ease !important;
        }}

        button[kind="secondary"]:hover, .stButton > button[kind="secondary"]:hover {{
            background: #f8fafc !important;
            border-color: #cbd5e1 !important;
            transform: translateY(-1px) !important;
        }}

        button:active {{
            transform: scale(0.98) !important;
        }}

        /* Clean Card Containers */
        [data-testid="stVerticalBlockBorderWrapper"] > div:has(> [data-testid="stVerticalBlock"]) {{
            background: #ffffff;
            border: 1px solid var(--line) !important;
            border-radius: 16px !important;
            box-shadow: var(--card-shadow) !important;
            padding: 1.15rem !important;
            transition: box-shadow 0.16s ease !important;
        }}

        /* Navigation Radio Styling */
        div.st-key-main_nav_radio [data-testid="stRadio"] label > div:first-child {{
            display: none !important;
        }}

        div.st-key-main_nav_radio [data-testid="stRadio"] label {{
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            border: 1px solid var(--line) !important;
            border-radius: 12px !important;
            background: #ffffff !important;
            cursor: pointer !important;
            transition: all 0.15s ease-in-out !important;
            margin: 0 !important;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04) !important;
        }}

        div.st-key-main_nav_radio [data-testid="stRadio"] label p {{
            color: var(--ink-secondary) !important;
            font-weight: 600 !important;
            margin: 0 !important;
            padding: 0 !important;
            line-height: 1.2 !important;
            text-align: center !important;
            white-space: nowrap !important;
        }}

        div.st-key-main_nav_radio [data-testid="stRadio"] label:has(input:checked) {{
            border-color: var(--primary) !important;
            background: var(--primary) !important;
            box-shadow: 0 4px 14px rgba(5, 150, 105, 0.3) !important;
        }}

        div.st-key-main_nav_radio [data-testid="stRadio"] label:has(input:checked) p {{
            color: #ffffff !important;
            font-weight: 700 !important;
        }}

        div.st-key-main_nav_radio [data-testid="stRadio"] label:not(:has(input:checked)):hover {{
            background: #f8fafc !important;
            border-color: #cbd5e1 !important;
        }}

        {nav_styles}

        /* Budget Banner Card */
        .budget-banner {{
            background: linear-gradient(135deg, #064e3b 0%, #065f46 55%, #047857 100%);
            color: #ffffff;
            border-radius: 16px;
            padding: 1.25rem 1.4rem;
            margin-bottom: 1.2rem;
            box-shadow: 0 10px 28px rgba(6, 78, 59, 0.25);
            position: relative;
            overflow: hidden;
        }}

        .budget-banner::after {{
            content: "";
            position: absolute;
            top: -40%;
            right: -20%;
            width: 250px;
            height: 250px;
            background: radial-gradient(circle, rgba(255,255,255,0.12) 0%, transparent 70%);
            border-radius: 50%;
            pointer-events: none;
        }}

        .budget-banner h3 {{
            color: #ffffff !important;
            margin: 0 0 0.3rem 0;
            font-size: 1.25rem;
            font-weight: 700;
        }}

        /* Transaction List Items */
        .tx-item {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0.85rem 0.5rem;
            border-bottom: 1px solid #f1f5f9;
            transition: background 0.12s ease;
        }}

        .tx-item:hover {{
            background: #f8fafc;
            border-radius: 10px;
        }}

        .tx-item:last-child {{
            border-bottom: none;
        }}

        .tx-left {{
            display: flex;
            align-items: center;
            gap: 0.9rem;
        }}

        .tx-icon {{
            width: 44px;
            height: 44px;
            border-radius: 12px;
            background: #ecfdf5;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.35rem;
            flex-shrink: 0;
            box-shadow: 0 2px 6px rgba(5, 150, 105, 0.08);
        }}

        .tx-icon-income {{
            background: #f0fdf4;
        }}

        .tx-icon-expense {{
            background: #fef2f2;
        }}

        .tx-title {{
            font-weight: 600;
            font-size: 0.96rem;
            color: var(--ink);
            display: flex;
            align-items: center;
            gap: 0.45rem;
        }}

        .tx-sub {{
            font-size: 0.8rem;
            color: var(--muted);
            margin-top: 2px;
        }}

        .tx-amount {{
            text-align: right;
            font-weight: 700;
            font-size: 1.05rem;
            letter-spacing: -0.01em;
        }}

        /* Badges */
        .badge-income {{
            background: #dcfce7;
            color: #15803d;
            padding: 2px 7px;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.75rem;
        }}

        .badge-expense {{
            background: #fee2e2;
            color: #b91c1c;
            padding: 2px 7px;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.75rem;
        }}

        .badge-pill {{
            background: #f1f5f9;
            color: #475569;
            padding: 3px 9px;
            border-radius: 20px;
            font-weight: 600;
            font-size: 0.78rem;
        }}

        /* Quick Amount Chips */
        .chip-button button {{
            min-height: 38px !important;
            font-size: 0.88rem !important;
            border-radius: 10px !important;
            padding: 0.35rem 0.6rem !important;
        }}

        /* Hide Streamlit footer branding */
        #MainMenu {{visibility: hidden;}}
        footer {{visibility: hidden;}}

        @media (max-width: 640px) {{
            [data-testid="stMainBlockContainer"] {{
                padding-left: 0.85rem !important;
                padding-right: 0.85rem !important;
            }}
            [data-testid="stMetricValue"] {{
                font-size: 1.3rem !important;
            }}
            div.st-key-main_nav_radio [data-testid="stRadio"] label p {{
                font-size: 0.72rem !important;
                white-space: normal !important;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ============================================================================
# OVERVIEW VIEW (High-Visibility Fintech Dashboard)
# ============================================================================

def render_overview(
    transactions: pd.DataFrame,
    date_preset: str,
    custom_dates: Optional[Tuple[date, date]],
    currency: str,
    exchange_rate: float,
    settings: Dict[str, Any],
) -> None:
    # 1. Resolve date range
    if date_preset == "Custom" and custom_dates and len(custom_dates) == 2:
        active_range = custom_dates
        period_name = f"{custom_dates[0].strftime('%d %b')} – {custom_dates[1].strftime('%d %b %Y')}"
    else:
        active_range = get_date_preset_range(date_preset)
        if active_range:
            period_name = f"{active_range[0].strftime('%d %b')} – {active_range[1].strftime('%d %b %Y')}"
        else:
            period_name = "All Time"

    visible = display_transactions(filtered_transactions(transactions, active_range), currency, exchange_rate)
    income = visible.loc[visible["type"] == "Income", "amount"].sum()
    expense = visible.loc[visible["type"] == "Expense", "amount"].sum()
    balance = income - expense
    savings_rate = ((balance / income) * 100) if income > 0 else 0

    # Days count calculation for daily pace
    if active_range:
        today = date.today()
        period_start, period_end = active_range
        eff_end = min(today, period_end)
        days_count = max(1, (eff_end - period_start).days + 1)
    else:
        days_count = max(1, (visible["date"].max() - visible["date"].min()).days + 1) if not visible.empty else 1

    daily_avg_spend = expense / days_count

    # Header Card with Quick Currency Switcher
    st.markdown(
        f"""
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 0.8rem;">
            <div>
                <h2 style="margin:0; font-size:1.55rem; font-weight:800; letter-spacing:-0.03em;">Financial Overview</h2>
                <div style="font-size:0.85rem; color:#64748b; margin-top:2px;">
                    📅 <strong>{period_name}</strong> &nbsp;•&nbsp; 
                    <span class="badge-pill">{len(visible)} transactions</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # --- KPI Grid (4 High-Contrast Cards) ---
    kpi_col1, kpi_col2 = st.columns(2)
    with kpi_col1:
        st.metric(
            label="Net Balance",
            value=format_currency(balance, currency),
            delta=f"{savings_rate:.0f}% saved" if income > 0 else None,
        )
    with kpi_col2:
        st.metric(
            label="Total Spent",
            value=format_currency(expense, currency),
            delta=f"{len(visible[visible['type'] == 'Expense'])} expenses",
            delta_color="inverse",
        )

    kpi_col3, kpi_col4 = st.columns(2)
    with kpi_col3:
        st.metric(
            label="Total Income",
            value=format_currency(income, currency),
            delta=f"{len(visible[visible['type'] == 'Income'])} deposits",
        )
    with kpi_col4:
        st.metric(
            label="Daily Average",
            value=format_currency(daily_avg_spend, currency),
            delta=f"Over {days_count} days",
            delta_color="off",
        )

    # --- Monthly Budget Banner ---
    monthly_budget = convert_usd_budget(float(settings.get("monthly_budget_usd", 1000.0)), currency, exchange_rate)
    if monthly_budget > 0:
        today = date.today()
        first_of_month = today.replace(day=1)
        _, days_in_month = calendar.monthrange(today.year, today.month)
        last_of_month = today.replace(day=days_in_month)

        this_month_tx = display_transactions(
            filtered_transactions(transactions, (first_of_month, last_of_month)),
            currency,
            exchange_rate,
        )
        curr_month_expense = this_month_tx.loc[this_month_tx["type"] == "Expense", "amount"].sum()
        pct_used = (curr_month_expense / monthly_budget) * 100 if monthly_budget > 0 else 0
        remaining_budget = monthly_budget - curr_month_expense
        days_left = max(1, days_in_month - today.day + 1)
        daily_allowance = max(0.0, remaining_budget / days_left)

        status_text = "On track" if pct_used < 75 else ("Approaching limit" if pct_used < 100 else "Over budget!")
        status_color = "#34d399" if pct_used < 75 else ("#fbbf24" if pct_used < 100 else "#f87171")

        st.markdown(
            f"""
            <div class="budget-banner">
                <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                    <div>
                        <div style="font-size:0.75rem; text-transform:uppercase; letter-spacing:0.08em; opacity:0.85;">This Month's Budget Tracker</div>
                        <h3 style="font-size:1.4rem; margin:0.25rem 0;">{format_currency(curr_month_expense, currency)} <span style="font-size:0.92rem; font-weight:normal; opacity:0.85;">of {format_currency(monthly_budget, currency)}</span></h3>
                    </div>
                    <div style="text-align:right;">
                        <span style="background:rgba(255,255,255,0.2); padding:5px 12px; border-radius:20px; font-size:0.82rem; font-weight:700; color:{status_color}; border:1px solid rgba(255,255,255,0.15);">
                            {status_text}
                        </span>
                        <div style="font-size:0.88rem; font-weight:600; margin-top:0.35rem;">{pct_used:.0f}% used</div>
                    </div>
                </div>
                <div style="margin-top:0.85rem; background:rgba(255,255,255,0.22); border-radius:10px; height:9px; overflow:hidden;">
                    <div style="width:{min(100.0, max(2.0, pct_used))}%; background:{status_color}; height:100%; border-radius:10px; transition:width 0.4s ease;"></div>
                </div>
                <div style="display:flex; justify-content:space-between; margin-top:0.75rem; font-size:0.84rem; opacity:0.95;">
                    <span>Remaining: <strong>{format_currency(remaining_budget, currency)}</strong></span>
                    <span>Daily allowance: <strong>{format_currency(daily_allowance, currency)}/day</strong> ({days_left}d left)</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # --- Cash Flow Visual Chart ---
    if not visible.empty:
        with st.container(border=True):
            st.markdown("<div style='font-weight:700; font-size:1.05rem; margin-bottom:0.4rem;'>Cash Flow Trend</div>", unsafe_allow_html=True)
            daily_flow = (
                visible.assign(
                    Income=visible["amount"].where(visible["type"] == "Income", 0.0),
                    Expense=visible["amount"].where(visible["type"] == "Expense", 0.0),
                )
                .groupby("date")[["Income", "Expense"]]
                .sum()
                .reset_index()
                .sort_values("date")
            )

            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=daily_flow["date"],
                y=daily_flow["Income"],
                name="Income",
                marker_color="#10b981",
                marker_line_width=0,
                hovertemplate="Income: %{y:,.2f}<extra></extra>",
            ))
            fig.add_trace(go.Bar(
                x=daily_flow["date"],
                y=daily_flow["Expense"],
                name="Expense",
                marker_color="#ef4444",
                marker_line_width=0,
                hovertemplate="Expense: %{y:,.2f}<extra></extra>",
            ))
            fig.update_layout(
                barmode="group",
                margin=dict(l=10, r=10, t=15, b=10),
                height=250,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                yaxis=dict(showgrid=True, gridcolor="#f1f5f9", tickprefix=f"{CURRENCY_SYMBOLS.get(currency, currency)} "),
                xaxis=dict(showgrid=False),
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False, "responsive": True})

    # --- Category Breakdown & Recent Transactions ---
    col_chart, col_recent = st.columns([1, 1])

    with col_chart:
        with st.container(border=True):
            st.markdown("<div style='font-weight:700; font-size:1.05rem; margin-bottom:0.5rem;'>Top Spending Categories</div>", unsafe_allow_html=True)
            exp_data = visible[visible["type"] == "Expense"]
            if exp_data.empty:
                st.caption("No expenses recorded for this period.")
            else:
                cat_summary = (
                    exp_data.groupby("category")["amount"]
                    .sum()
                    .reset_index()
                    .sort_values("amount", ascending=False)
                )
                fig_cat = px.pie(
                    cat_summary,
                    values="amount",
                    names="category",
                    hole=0.6,
                    color_discrete_sequence=px.colors.qualitative.Prism,
                )
                fig_cat.update_traces(
                    textposition="inside",
                    textinfo="percent+label",
                    hovertemplate="%{label}: " + CURRENCY_SYMBOLS.get(currency, currency) + " %{value:,.2f} (%{percent})<extra></extra>",
                )
                fig_cat.update_layout(
                    margin=dict(l=10, r=10, t=10, b=10),
                    height=250,
                    showlegend=False,
                    paper_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig_cat, use_container_width=True, config={"displayModeBar": False, "responsive": True})

    with col_recent:
        with st.container(border=True):
            st.markdown("<div style='font-weight:700; font-size:1.05rem; margin-bottom:0.5rem;'>Recent Activity</div>", unsafe_allow_html=True)
            if visible.empty:
                st.caption("No transactions found. Tap 'Quick Add' to log one!")
            else:
                latest = visible.sort_values(["date", "created_at"], ascending=False).head(5)
                for _, row in latest.iterrows():
                    is_inc = row["type"] == "Income"
                    sign = "+" if is_inc else "-"
                    amt_color = "#16a34a" if is_inc else "#dc2626"
                    icon = get_category_icon(str(row["category"]))
                    badge_class = "badge-income" if is_inc else "badge-expense"
                    icon_class = "tx-icon-income" if is_inc else "tx-icon-expense"
                    formatted_dt = row["date"].strftime("%d %b") if pd.notna(row["date"]) else ""

                    st.markdown(
                        f"""
                        <div class="tx-item">
                            <div class="tx-left">
                                <div class="tx-icon {icon_class}">{icon}</div>
                                <div>
                                    <div class="tx-title">{row['category']} <span class="{badge_class}">{row['type']}</span></div>
                                    <div class="tx-sub">{formatted_dt} • {row['detail']} • {row['payment_method']}</div>
                                </div>
                            </div>
                            <div class="tx-amount" style="color:{amt_color};">
                                {sign}{format_currency(row['amount'], currency)}
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )


# ============================================================================
# QUICK ADD VIEW (Fast 1-Tap Ergonomic Flow)
# ============================================================================

def render_quick_add(currency: str) -> None:
    st.markdown(
        """
        <div>
            <h2 style="margin:0 0 0.2rem 0; font-size:1.55rem; font-weight:800; letter-spacing:-0.03em;">Record Transaction</h2>
            <div style="font-size:0.85rem; color:#64748b; margin-bottom: 1rem;">Log spending or earnings in seconds.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if "add_amount" not in st.session_state:
        st.session_state.add_amount = 0.0

    # Type Selector
    tx_type = st.segmented_control(
        "Transaction Type",
        options=["Expense", "Income"],
        default="Expense",
        key="quick_add_type",
        label_visibility="collapsed",
    )

    with st.container(border=True):
        # Quick Denomination Buttons tailored to Currency
        st.markdown(f"<div style='font-size:0.82rem; font-weight:600; color:#475569; margin-bottom:4px;'>⚡ Quick Amount Chips ({CURRENCY_SYMBOLS.get(currency, currency)}):</div>", unsafe_allow_html=True)
        q_cols = st.columns(6)
        if currency == "USD":
            chips = [5.0, 10.0, 20.0, 50.0, 100.0]
        else:
            chips = [50.0, 100.0, 200.0, 500.0, 1000.0]

        for i, val in enumerate(chips):
            with q_cols[i]:
                if st.button(f"+{int(val)}", key=f"chip_{val}", use_container_width=True):
                    st.session_state.add_amount = round(st.session_state.add_amount + val, 2)
                    st.rerun()

        with q_cols[5]:
            if st.button("↺ Reset", key="chip_reset", use_container_width=True):
                st.session_state.add_amount = 0.0
                st.rerun()

        # Amount Input (Stored in ETB)
        amount = st.number_input(
            "Amount (ETB)",
            min_value=0.01,
            value=max(0.01, float(st.session_state.add_amount)) if st.session_state.add_amount > 0 else 50.00,
            step=5.0,
            format="%.2f",
            key="input_amount",
        )

        # Quick Date Option
        date_col1, date_col2 = st.columns([1, 1])
        with date_col1:
            date_shortcut = st.segmented_control(
                "Date",
                options=["Today", "Yesterday", "Custom"],
                default="Today",
                key="date_shortcut",
                label_visibility="collapsed",
            )
        with date_col2:
            if date_shortcut == "Today":
                tx_date = date.today()
                st.caption(f"🗓️ {tx_date.strftime('%A, %d %B %Y')}")
            elif date_shortcut == "Yesterday":
                tx_date = date.today() - timedelta(days=1)
                st.caption(f"🗓️ {tx_date.strftime('%A, %d %B %Y')}")
            else:
                tx_date = st.date_input("Select Date", value=date.today(), key="input_date", label_visibility="collapsed")

        # Category & Recipient/Source Selection
        if tx_type == "Expense":
            st.markdown("<div style='font-size:0.85rem; font-weight:600; color:#334155; margin-top:0.4rem; margin-bottom:0.2rem;'>Category:</div>", unsafe_allow_html=True)
            cat_options = [f"{get_category_icon(c)} {c}" for c in EXPENSE_CATEGORIES]
            selected_cat_raw = st.selectbox(
                "Category",
                options=cat_options,
                index=0,
                label_visibility="collapsed",
            )
            clean_category = selected_cat_raw.split(" ", 1)[1] if " " in selected_cat_raw else selected_cat_raw

            st.markdown("<div style='font-size:0.85rem; font-weight:600; color:#334155; margin-top:0.4rem; margin-bottom:0.2rem;'>Paid For (Beneficiary):</div>", unsafe_allow_html=True)
            detail = st.selectbox("Paid For", options=EXPENSE_RECIPIENTS, index=0, label_visibility="collapsed")
        else:
            clean_category = "Income"
            st.markdown("<div style='font-size:0.85rem; font-weight:600; color:#334155; margin-top:0.4rem; margin-bottom:0.2rem;'>Income Source:</div>", unsafe_allow_html=True)
            detail = st.selectbox("Income Source", options=INCOME_SOURCES, index=0, label_visibility="collapsed")

        # Payment Method
        st.markdown("<div style='font-size:0.85rem; font-weight:600; color:#334155; margin-top:0.4rem; margin-bottom:0.2rem;'>Payment Channel:</div>", unsafe_allow_html=True)
        pay_options = [f"{get_payment_icon(p)} {p}" for p in PAYMENT_METHODS]
        selected_pay_raw = st.selectbox("Payment Method", options=pay_options, index=0, label_visibility="collapsed")
        clean_payment = selected_pay_raw.split(" ", 1)[1] if " " in selected_pay_raw else selected_pay_raw

        # Notes
        notes = st.text_input("Notes (Optional)", placeholder="e.g. Lunch with team, monthly electric bill, groceries...")

        st.markdown("<div style='margin-top:0.6rem;'></div>", unsafe_allow_html=True)
        if st.button(f"Save {tx_type}", type="primary", use_container_width=True, icon=":material/check:"):
            if amount <= 0:
                st.error("Please enter a valid amount greater than 0.")
            else:
                add_transaction(
                    transaction_type=tx_type,
                    amount=float(amount),
                    transaction_date=tx_date,
                    category=clean_category,
                    detail=detail,
                    payment_method=clean_payment,
                    notes=notes,
                )
                st.session_state.add_amount = 0.0
                st.toast(f"✅ {tx_type} saved: {format_currency(float(amount), 'ETB')}", icon="🎉")
                st.session_state.app_view = "📊 Overview"
                st.rerun()


# ============================================================================
# ANALYTICS VIEW
# ============================================================================

def render_analytics(transactions: pd.DataFrame, currency: str, exchange_rate: float) -> None:
    st.markdown(
        """
        <div>
            <h2 style="margin:0 0 0.2rem 0; font-size:1.55rem; font-weight:800; letter-spacing:-0.03em;">Deep Spending Analytics</h2>
            <div style="font-size:0.85rem; color:#64748b; margin-bottom: 0.8rem;">Break down where your money flows and optimize your spending habits.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if transactions.empty:
        st.info("No transaction data available. Add transactions to see analytics.")
        return

    # Period Filter Chips
    period_choice = st.segmented_control(
        "Period Filter",
        options=["This Month", "Last Month", "Last 30 Days", "This Year", "All Time"],
        default="This Month",
        key="analytics_period",
        label_visibility="collapsed",
    )

    active_range = get_date_preset_range(period_choice)
    visible = display_transactions(filtered_transactions(transactions, active_range), currency, exchange_rate)
    expenses = visible[visible["type"] == "Expense"]
    incomes = visible[visible["type"] == "Income"]

    total_expense = expenses["amount"].sum()
    total_income = incomes["amount"].sum()

    if expenses.empty and incomes.empty:
        st.caption(f"No transactions recorded for {period_choice}.")
        return

    # Financial Health Summary
    h_col1, h_col2, h_col3 = st.columns(3)
    with h_col1:
        st.metric("Total Expenses", format_currency(total_expense, currency))
    with h_col2:
        top_cat = expenses.groupby("category")["amount"].sum().idxmax() if not expenses.empty else "N/A"
        top_cat_amt = expenses.groupby("category")["amount"].sum().max() if not expenses.empty else 0.0
        st.metric("Top Category", top_cat, delta=format_currency(top_cat_amt, currency), delta_color="off")
    with h_col3:
        avg_exp = expenses["amount"].mean() if not expenses.empty else 0.0
        st.metric("Avg Expense Ticket", format_currency(avg_exp, currency))

    # Category Breakdown & Share
    with st.container(border=True):
        st.markdown("<div style='font-weight:700; font-size:1.05rem;'>Category Breakdown & Share</div>", unsafe_allow_html=True)
        if not expenses.empty:
            cat_df = expenses.groupby("category")["amount"].sum().reset_index().sort_values("amount", ascending=False)
            cat_df["share"] = (cat_df["amount"] / total_expense) * 100 if total_expense > 0 else 0

            fig_donut = px.pie(
                cat_df,
                values="amount",
                names="category",
                hole=0.6,
                color_discrete_sequence=px.colors.qualitative.Safe,
            )
            fig_donut.update_traces(
                textposition="outside",
                textinfo="percent+label",
                hovertemplate="%{label}: " + CURRENCY_SYMBOLS.get(currency, currency) + " %{value:,.2f} (%{percent})<extra></extra>",
            )
            fig_donut.update_layout(
                margin=dict(l=10, r=10, t=10, b=10),
                height=280,
                showlegend=False,
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_donut, use_container_width=True, config={"displayModeBar": False, "responsive": True})

            # Ranked list
            st.markdown("<div style='font-size:0.85rem; font-weight:700; color:#475569; margin-top:0.4rem;'>Ranked Spending:</div>", unsafe_allow_html=True)
            for _, r in cat_df.iterrows():
                icon = get_category_icon(r["category"])
                st.markdown(
                    f"""
                    <div style="display:flex; justify-content:space-between; align-items:center; padding:6px 0; font-size:0.9rem; border-bottom:1px solid #f1f5f9;">
                        <span>{icon} {r['category']}</span>
                        <span><strong>{format_currency(r['amount'], currency)}</strong> <span style="color:#64748b; font-size:0.8rem;">({r['share']:.1f}%)</span></span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.caption("No expenses recorded in this period.")

    # Day-of-Week Spending Heat & Beneficiaries
    col1, col2 = st.columns(2)

    with col1:
        with st.container(border=True):
            st.markdown("<div style='font-weight:700; font-size:1.02rem; margin-bottom:0.4rem;'>Spending by Day of Week</div>", unsafe_allow_html=True)
            if not expenses.empty:
                exp_with_day = expenses.copy()
                exp_with_day["day_name"] = pd.to_datetime(exp_with_day["date"]).dt.day_name()
                day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                day_df = exp_with_day.groupby("day_name")["amount"].sum().reindex(day_order).fillna(0.0).reset_index()

                fig_day = px.bar(
                    day_df,
                    x="day_name",
                    y="amount",
                    color_discrete_sequence=["#059669"],
                )
                fig_day.update_layout(
                    margin=dict(l=5, r=5, t=10, b=5),
                    height=210,
                    xaxis_title=None,
                    yaxis_title=None,
                    yaxis=dict(tickprefix=f"{CURRENCY_SYMBOLS.get(currency, currency)} ", showgrid=True, gridcolor="#f1f5f9"),
                    xaxis=dict(tickangle=-25),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig_day, use_container_width=True, config={"displayModeBar": False, "responsive": True})

    with col2:
        with st.container(border=True):
            st.markdown("<div style='font-weight:700; font-size:1.02rem; margin-bottom:0.4rem;'>Beneficiaries (Paid For)</div>", unsafe_allow_html=True)
            if not expenses.empty:
                rec_df = expenses.groupby("detail")["amount"].sum().reset_index().sort_values("amount", ascending=False)
                fig_rec = px.bar(
                    rec_df,
                    x="amount",
                    y="detail",
                    orientation="h",
                    color_discrete_sequence=["#0284c7"],
                )
                fig_rec.update_layout(
                    margin=dict(l=5, r=5, t=10, b=5),
                    height=210,
                    xaxis_title=None,
                    yaxis_title=None,
                    xaxis=dict(tickprefix=f"{CURRENCY_SYMBOLS.get(currency, currency)} ", showgrid=True, gridcolor="#f1f5f9"),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig_rec, use_container_width=True, config={"displayModeBar": False, "responsive": True})

    # Payment Channels & Top Purchases
    p_col1, p_col2 = st.columns(2)
    with p_col1:
        with st.container(border=True):
            st.markdown("<div style='font-weight:700; font-size:1.02rem; margin-bottom:0.4rem;'>Payment Channels</div>", unsafe_allow_html=True)
            if not expenses.empty:
                pay_df = expenses.groupby("payment_method")["amount"].sum().reset_index().sort_values("amount", ascending=False)
                fig_pay = px.bar(
                    pay_df,
                    x="payment_method",
                    y="amount",
                    color_discrete_sequence=["#7c3aed"],
                )
                fig_pay.update_layout(
                    margin=dict(l=5, r=5, t=10, b=5),
                    height=210,
                    xaxis_title=None,
                    yaxis_title=None,
                    yaxis=dict(tickprefix=f"{CURRENCY_SYMBOLS.get(currency, currency)} ", showgrid=True, gridcolor="#f1f5f9"),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig_pay, use_container_width=True, config={"displayModeBar": False, "responsive": True})

    with p_col2:
        with st.container(border=True):
            st.markdown("<div style='font-weight:700; font-size:1.02rem; margin-bottom:0.4rem;'>🔥 Largest Purchases</div>", unsafe_allow_html=True)
            if not expenses.empty:
                top_expenses = expenses.sort_values("amount", ascending=False).head(4)
                for _, row in top_expenses.iterrows():
                    icon = get_category_icon(str(row["category"]))
                    note_str = f" • {row['notes']}" if row['notes'] else ""
                    st.markdown(
                        f"""
                        <div class="tx-item">
                            <div class="tx-left">
                                <div class="tx-icon tx-icon-expense">{icon}</div>
                                <div>
                                    <div class="tx-title">{row['category']}</div>
                                    <div class="tx-sub">{row['date'].strftime('%d %b %Y')} • {row['detail']}{note_str}</div>
                                </div>
                            </div>
                            <div class="tx-amount" style="color:#dc2626;">
                                -{format_currency(row['amount'], currency)}
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )


# ============================================================================
# BUDGET & TARGET VIEW
# ============================================================================

def render_budget(transactions: pd.DataFrame, currency: str, exchange_rate: float, settings: Dict[str, Any]) -> None:
    st.markdown(
        """
        <div>
            <h2 style="margin:0 0 0.2rem 0; font-size:1.55rem; font-weight:800; letter-spacing:-0.03em;">Budget & Goals</h2>
            <div style="font-size:0.85rem; color:#64748b; margin-bottom: 0.8rem;">Set monthly spending limits and stay disciplined across categories.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    curr_budget_usd = float(settings.get("monthly_budget_usd", 1000.0))
    curr_budget = convert_usd_budget(curr_budget_usd, currency, exchange_rate)

    # Monthly Target Setup
    with st.container(border=True):
        st.markdown("<div style='font-weight:700; font-size:1.05rem; margin-bottom:0.5rem;'>Overall Monthly Budget</div>", unsafe_allow_html=True)
        new_budget = st.number_input(
            f"Set Monthly Budget Limit ({CURRENCY_SYMBOLS.get(currency, currency)})",
            min_value=0.0,
            value=curr_budget,
            step=100.0,
            format="%.2f",
        )
        if st.button("Save Monthly Budget Target", type="primary", use_container_width=True):
            settings["monthly_budget_usd"] = (
                float(new_budget) / exchange_rate if currency == "ETB" else float(new_budget)
            )
            save_user_settings(settings)
            st.toast("🎯 Budget target updated successfully!", icon="🎯")
            st.rerun()

    # Budget Calculation for Current Month
    today = date.today()
    start_of_month = today.replace(day=1)
    _, days_in_month = calendar.monthrange(today.year, today.month)
    end_of_month = today.replace(day=days_in_month)

    month_tx = filtered_transactions(transactions, (start_of_month, end_of_month))
    month_tx = display_transactions(month_tx, currency, exchange_rate)
    month_expense = month_tx.loc[month_tx["type"] == "Expense", "amount"].sum()

    days_passed = today.day
    days_remaining = max(1, days_in_month - days_passed + 1)
    pct_spent = (month_expense / new_budget * 100) if new_budget > 0 else 0
    remaining_balance = new_budget - month_expense
    daily_budget = max(0.0, remaining_balance / days_remaining)

    with st.container(border=True):
        st.markdown("<div style='font-weight:700; font-size:1.05rem; margin-bottom:0.5rem;'>Current Month Performance</div>", unsafe_allow_html=True)
        b_col1, b_col2 = st.columns(2)
        with b_col1:
            st.metric("Spent So Far", format_currency(month_expense, currency), delta=f"{pct_spent:.0f}% of budget", delta_color="inverse")
        with b_col2:
            st.metric("Remaining Budget", format_currency(remaining_balance, currency), delta=f"{days_remaining} days left")

        prog_val = min(1.0, max(0.0, pct_spent / 100.0))
        st.progress(prog_val)

        if pct_spent >= 100:
            st.error(f"🚨 You have exceeded your monthly budget by {format_currency(abs(remaining_balance), currency)}!")
        elif pct_spent >= 80:
            st.warning(f"⚠️ Caution: You've used {pct_spent:.0f}% of your budget. Spend cautiously for the rest of the month.")
        else:
            st.success(f"✅ Great job! You are pacing well with {format_currency(daily_budget, currency)}/day remaining.")

    # Category-specific spending & limits
    with st.container(border=True):
        st.markdown("<div style='font-weight:700; font-size:1.05rem; margin-bottom:0.6rem;'>Spending by Category This Month</div>", unsafe_allow_html=True)
        if month_tx.empty:
            st.caption("No entries recorded this month.")
        else:
            cat_month = (
                month_tx[month_tx["type"] == "Expense"]
                .groupby("category")["amount"]
                .sum()
                .reset_index()
                .sort_values("amount", ascending=False)
            )
            cat_budgets = settings.get("category_budgets", {})
            for _, r in cat_month.iterrows():
                cat_name = r["category"]
                spent_amt = r["amount"]
                icon = get_category_icon(cat_name)
                cat_target_usd = float(cat_budgets.get(cat_name, 0.0))
                cat_target = convert_usd_budget(cat_target_usd, currency, exchange_rate)

                target_str = f" / {format_currency(cat_target, currency)}" if cat_target > 0 else ""
                cat_share = (spent_amt / month_expense * 100) if month_expense > 0 else 0

                st.markdown(
                    f"""
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.4rem; font-size:0.92rem;">
                        <span>{icon} <strong>{cat_name}</strong></span>
                        <span><strong>{format_currency(spent_amt, currency)}</strong>{target_str} <span style="font-size:0.8rem; color:#64748b;">({cat_share:.0f}%)</span></span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if cat_target > 0:
                    cat_pct = min(1.0, spent_amt / cat_target)
                    st.progress(cat_pct)

        with st.expander("⚙️ Customize Category Budget Limits", expanded=False):
            st.caption("Assign monthly spending limits for individual categories.")
            cat_budgets = settings.get("category_budgets", {})
            edited_budgets = {}
            for cat in EXPENSE_CATEGORIES:
                curr_cat_val_usd = float(cat_budgets.get(cat, 0.0))
                curr_cat_val = convert_usd_budget(curr_cat_val_usd, currency, exchange_rate)
                val = st.number_input(f"{get_category_icon(cat)} {cat} ({CURRENCY_SYMBOLS.get(currency, currency)})", min_value=0.0, value=curr_cat_val, step=50.0, key=f"cat_b_{cat}")
                if val > 0:
                    edited_budgets[cat] = float(val) / exchange_rate if currency == "ETB" else float(val)
            if st.button("Save Category Limits", type="primary", use_container_width=True):
                settings["category_budgets"] = edited_budgets
                save_user_settings(settings)
                st.toast("Category limits saved!", icon="💾")
                st.rerun()


# ============================================================================
# LEDGER VIEW (Search, Filter, Edit, Delete, Export)
# ============================================================================

def render_ledger(transactions: pd.DataFrame, currency: str, exchange_rate: float) -> None:
    st.markdown(
        """
        <div>
            <h2 style="margin:0 0 0.2rem 0; font-size:1.55rem; font-weight:800; letter-spacing:-0.03em;">Transaction Ledger</h2>
            <div style="font-size:0.85rem; color:#64748b; margin-bottom: 0.8rem;">Review, edit, delete, or export your financial records.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Search and Filter Section
    with st.container(border=True):
        search = st.text_input("🔍 Search ledger", placeholder="Search by note, category, recipient, method...", key="ledger_search")

        f_col1, f_col2 = st.columns(2)
        with f_col1:
            type_filter = st.segmented_control("Filter by Type", options=["All", "Expense", "Income"], default="All", key="filter_type")
        with f_col2:
            all_categories = ["All"] + sorted(list(transactions["category"].dropna().unique())) if not transactions.empty else ["All"]
            cat_filter = st.selectbox("Category", options=all_categories, key="filter_category")

    # Apply filters
    visible = filtered_transactions(
        transactions,
        tx_type=type_filter if type_filter != "All" else None,
        category=cat_filter if cat_filter != "All" else None,
        search_term=search,
    )
    display_visible = display_transactions(visible, currency, exchange_rate)
    total_visible_exp = display_visible.loc[display_visible["type"] == "Expense", "amount"].sum()
    total_visible_inc = display_visible.loc[display_visible["type"] == "Income", "amount"].sum()

    st.markdown(
        f"""
        <div style='font-size:0.86rem; color:#475569; margin: 0.6rem 0; display:flex; justify-content:space-between; align-items:center;'>
            <span>Showing <strong>{len(visible)}</strong> of {len(transactions)} entries</span>
            <span>Expenses: <strong style="color:#dc2626;">{format_currency(total_visible_exp, currency)}</strong> &nbsp;|&nbsp; Income: <strong style="color:#16a34a;">{format_currency(total_visible_inc, currency)}</strong></span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if visible.empty:
        st.info("No matching transactions found.")
    else:
        sorted_visible = display_visible.sort_values(["date", "created_at"], ascending=False)
        for _, row in sorted_visible.iterrows():
            tx_id = str(row["id"])
            is_income = row["type"] == "Income"
            amt_color = "#16a34a" if is_income else "#dc2626"
            sign = "+" if is_income else "-"
            icon = get_category_icon(str(row["category"]))
            badge_class = "badge-income" if is_income else "badge-expense"
            icon_class = "tx-icon-income" if is_income else "tx-icon-expense"
            formatted_date = row["date"].strftime("%d %b %Y") if pd.notna(row["date"]) else ""

            with st.container(border=True):
                col_left, col_right = st.columns([3, 1])
                with col_left:
                    st.markdown(
                        f"""
                        <div class="tx-left">
                            <div class="tx-icon {icon_class}">{icon}</div>
                            <div>
                                <div class="tx-title">{row['category']} <span class="{badge_class}">{row['type']}</span></div>
                                <div class="tx-sub">{formatted_date} • {row['detail']} • {row['payment_method']}</div>
                                {f'<div style="font-size:0.8rem; color:#475569; font-style:italic; margin-top:2px;">"{row["notes"]}"</div>' if row['notes'] else ''}
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with col_right:
                    st.markdown(
                        f"""
                        <div class="tx-amount" style="color:{amt_color}; margin-top:4px;">
                            {sign}{format_currency(row['amount'], currency)}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                # Edit and Delete Expander
                with st.expander("⚙️ Edit / Delete", expanded=False):
                    with st.form(f"edit_form_{tx_id}"):
                        e_type = st.selectbox("Type", ["Expense", "Income"], index=0 if row["type"] == "Expense" else 1, key=f"e_type_{tx_id}")
                        raw_amount = float(transactions.loc[transactions["id"] == tx_id, "amount"].iloc[0])
                        e_amount = st.number_input("Amount (ETB)", min_value=0.01, value=raw_amount, step=5.0, format="%.2f", key=f"e_amt_{tx_id}")
                        e_date = st.date_input("Date", value=row["date"] if pd.notna(row["date"]) else date.today(), key=f"e_date_{tx_id}")

                        if e_type == "Expense":
                            cat_idx = EXPENSE_CATEGORIES.index(row["category"]) if row["category"] in EXPENSE_CATEGORIES else 0
                            e_cat = st.selectbox("Category", EXPENSE_CATEGORIES, index=cat_idx, key=f"e_cat_{tx_id}")
                            rec_idx = EXPENSE_RECIPIENTS.index(row["detail"]) if row["detail"] in EXPENSE_RECIPIENTS else 0
                            e_detail = st.selectbox("Paid For", EXPENSE_RECIPIENTS, index=rec_idx, key=f"e_rec_{tx_id}")
                        else:
                            e_cat = "Income"
                            src_idx = INCOME_SOURCES.index(row["detail"]) if row["detail"] in INCOME_SOURCES else 0
                            e_detail = st.selectbox("Income Source", INCOME_SOURCES, index=src_idx, key=f"e_src_{tx_id}")

                        pay_idx = PAYMENT_METHODS.index(row["payment_method"]) if row["payment_method"] in PAYMENT_METHODS else 0
                        e_pay = st.selectbox("Payment Method", PAYMENT_METHODS, index=pay_idx, key=f"e_pay_{tx_id}")
                        e_notes = st.text_input("Notes", value=str(row["notes"]), key=f"e_notes_{tx_id}")

                        save_col, del_col = st.columns(2)
                        with save_col:
                            submitted_edit = st.form_submit_button("Save Changes", type="primary", use_container_width=True)
                        with del_col:
                            submitted_delete = st.form_submit_button("Delete Entry", type="secondary", use_container_width=True)

                        if submitted_edit:
                            update_transaction(
                                transaction_id=tx_id,
                                transaction_type=e_type,
                                amount=float(e_amount),
                                transaction_date=e_date,
                                category=e_cat,
                                detail=e_detail,
                                payment_method=e_pay,
                                notes=e_notes,
                            )
                            st.toast("Transaction updated!", icon="✏️")
                            st.rerun()

                        if submitted_delete:
                            delete_transaction(tx_id)
                            st.toast("Transaction deleted.", icon="🗑️")
                            st.rerun()

    # Export & Data Center
    st.markdown("<div style='margin-top:1rem;'></div>", unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown("<div style='font-weight:700; font-size:1.05rem; margin-bottom:0.5rem;'>Export & Data Management</div>", unsafe_allow_html=True)
        exp_col1, exp_col2 = st.columns(2)

        with exp_col1:
            csv_data = transactions.to_csv(index=False)
            st.download_button(
                label="Download CSV",
                data=csv_data,
                file_name=f"ledgerly_export_{date.today().isoformat()}.csv",
                mime="text/csv",
                icon=":material/download:",
                use_container_width=True,
            )

        with exp_col2:
            try:
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
                    transactions.to_excel(writer, sheet_name="Ledger", index=False)
                st.download_button(
                    label="Download Excel (.xlsx)",
                    data=buffer.getvalue(),
                    file_name=f"ledgerly_export_{date.today().isoformat()}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    icon=":material/table_view:",
                    use_container_width=True,
                )
            except Exception:
                pass


# ============================================================================
# MAIN APPLICATION CONTROLLER
# ============================================================================

def main() -> None:
    settings = load_user_settings()
    nav_position = settings.get("nav_position", "Bottom (Mobile)")
    inject_mobile_css(nav_position=nav_position)

    if not require_authentication():
        return

    # Load transactions
    transactions = load_transactions()

    # Session currency setup
    if "currency" not in st.session_state:
        st.session_state.currency = settings.get("display_currency", "ETB")

    currency = st.session_state.currency
    exchange_rate, rate_is_live = fetch_usd_to_etb_rate(settings)

    # Sidebar for Settings & Management
    with st.sidebar:
        st.markdown("### 💳 Ledgerly")
        st.caption("Personal Finance & Wealth Tracker")

        st.markdown("**Preferences**")
        selected_currency = st.selectbox(
            "Display Currency",
            options=CURRENCIES,
            index=CURRENCIES.index(currency) if currency in CURRENCIES else 0,
            key="sidebar_currency",
        )
        if selected_currency != st.session_state.currency:
            st.session_state.currency = selected_currency
            settings["display_currency"] = selected_currency
            save_user_settings(settings)
            st.rerun()

        selected_nav_pos = st.selectbox(
            "Navigation Position",
            options=["Bottom (Mobile)", "Top (Header)"],
            index=0 if "Bottom" in nav_position else 1,
            key="sidebar_nav_pos",
        )
        if selected_nav_pos != nav_position:
            settings["nav_position"] = selected_nav_pos
            save_user_settings(settings)
            st.rerun()

        st.divider()
        st.caption(f"💾 {len(transactions)} transactions safely preserved.")
        rate_label = "live Google Finance" if rate_is_live else "cached rate"
        st.caption(f"💱 1 USD = {exchange_rate:,.2f} ETB ({rate_label})")

        if st.button("🔄 Refresh Exchange Rate", use_container_width=True):
            fetch_usd_to_etb_rate(settings, force_refresh=True)
            st.toast("Exchange rate refreshed!", icon="🔄")
            st.rerun()

        st.divider()
        if st.button("Lock Ledger", icon=":material/lock:", use_container_width=True):
            st.session_state.authenticated = False
            st.rerun()

    # Navigation Dock / Bar
    nav_options = ["📊 Overview", "➕ Quick Add", "📈 Analytics", "🎯 Budget", "📑 Ledger"]
    if "app_view" not in st.session_state:
        st.session_state.app_view = "📊 Overview"

    active_view = st.radio(
        "Navigation",
        options=nav_options,
        index=nav_options.index(st.session_state.app_view),
        horizontal=True,
        key="main_nav_radio",
        label_visibility="collapsed",
    )
    if active_view and active_view != st.session_state.app_view:
        st.session_state.app_view = active_view

    current_view = st.session_state.app_view

    # Quick Period Filter for Overview
    date_preset = "This Month"
    custom_dates = None
    if current_view == "📊 Overview":
        f_col1, f_col2 = st.columns([1.6, 1])
        with f_col1:
            date_preset = st.segmented_control(
                "Period",
                options=["This Month", "Last Month", "Last 30 Days", "This Year", "All Time", "Custom"],
                default="This Month",
                label_visibility="collapsed",
                key="top_date_filter",
            )
            if not date_preset:
                date_preset = "This Month"
        with f_col2:
            if date_preset == "Custom":
                min_dt = transactions["date"].min() if not transactions.empty else date.today()
                max_dt = transactions["date"].max() if not transactions.empty else date.today()
                custom_dates = st.date_input("Date range", value=(min_dt, max_dt), label_visibility="collapsed")
                if not (isinstance(custom_dates, (tuple, list)) and len(custom_dates) == 2):
                    custom_dates = None

    # Render Active Screen
    if current_view == "📊 Overview":
        render_overview(transactions, date_preset, custom_dates, currency, exchange_rate, settings)
    elif current_view == "➕ Quick Add":
        render_quick_add(currency)
    elif current_view == "📈 Analytics":
        render_analytics(transactions, currency, exchange_rate)
    elif current_view == "🎯 Budget":
        render_budget(transactions, currency, exchange_rate, settings)
    elif current_view == "📑 Ledger":
        render_ledger(transactions, currency, exchange_rate)


if __name__ == "__main__":
    main()
