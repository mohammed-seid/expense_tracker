import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import hmac
import os
import json
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
    initial_sidebar_state="collapsed",  # Mobile-first: start collapsed so dashboard isn't obscured
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
        "category_budgets": {},
    }
    if not SETTINGS_FILE.exists():
        return default_settings
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            settings = {**default_settings, **data}
            # Older versions stored an unqualified budget and a display symbol.
            # Existing transaction amounts are ETB, so retain ETB as the initial view.
            if "monthly_budget_usd" not in data:
                settings["monthly_budget_usd"] = 1000.0
            if "display_currency" not in data:
                settings["display_currency"] = "ETB"
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

def backup_data() -> None:
    """Creates a local backup before modifying existing data."""
    try:
        if DATA_FILE.exists():
            shutil.copy2(DATA_FILE, BACKUP_FILE)
    except Exception:
        pass


def load_transactions() -> pd.DataFrame:
    """Safely loads transactions while preserving column integrity."""
    if not DATA_FILE.exists():
        return pd.DataFrame(columns=TRANSACTION_COLUMNS)
    try:
        transactions = pd.read_csv(DATA_FILE, dtype={"id": str})
    except (OSError, pd.errors.ParserError):
        return pd.DataFrame(columns=TRANSACTION_COLUMNS)

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
    backup_data()
    # Format date back to string ISO
    df_to_save = transactions.copy()
    df_to_save["date"] = pd.to_datetime(df_to_save["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df_to_save.to_csv(DATA_FILE, index=False)


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
    return f"{CURRENCY_SYMBOLS.get(currency_symbol, currency_symbol)}{amount:,.2f}"


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


def fetch_usd_to_etb_rate(settings: Dict[str, Any]) -> Tuple[float, bool]:
    """Fetches the USD/ETB quote from Google Finance, falling back to the last rate."""
    cached_rate = float(settings.get("exchange_rate_usd_to_etb", DEFAULT_USD_TO_ETB_RATE))
    try:
        response = requests.get(
            "https://www.google.com/finance/quote/USD-ETB",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        response.raise_for_status()
        page = response.text
        matches = re.findall(
            r'(?:data-last-price|data-price|price)\s*[:=]\s*["\']?([0-9]+(?:\.[0-9]+)?)',
            page,
            flags=re.IGNORECASE,
        )
        if not matches:
            # Google Finance currently embeds the quote in AF_initDataCallback data.
            matches = re.findall(r'0,0,([0-9]+(?:\.[0-9]+)?),4,1', page)
        rate = next(
            (float(value) for value in matches if 50.0 < float(value) < 500.0),
            0.0,
        )
        if rate <= 0:
            raise ValueError("Google Finance did not return a valid quote")
        settings["exchange_rate_usd_to_etb"] = rate
        settings["exchange_rate_updated_at"] = datetime.now().isoformat(timespec="seconds")
        save_user_settings(settings)
        return rate, True
    except (requests.RequestException, ValueError, TypeError, IndexError):
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
    # If no password configured, allow access without locking user out
    if not expected_password:
        return True

    if st.session_state.get("authenticated", False):
        return True

    st.markdown(
        """
        <div style="text-align: center; margin: 3rem 0 1.5rem;">
            <div style="font-size: 3rem; margin-bottom: 0.5rem;">💳</div>
            <h1 style="font-size: 1.8rem; margin-bottom: 0.3rem;">Ledgerly</h1>
            <p style="color: #64746a; font-size: 0.95rem;">Enter your password to unlock your personal finances.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        with st.form("login_form"):
            password = st.text_input("Password", type="password", placeholder="Enter password")
            submitted = st.form_submit_button("Unlock Ledger", type="primary", use_container_width=True)
            if submitted:
                if hmac.compare_digest(password, expected_password):
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("Incorrect password. Please try again.", icon="🚨")

    st.caption("<div style='text-align:center;'>Your ledger is encrypted and kept private on this device.</div>", unsafe_allow_html=True)
    return False


# ============================================================================
# MOBILE-FIRST CSS
# ============================================================================

def inject_mobile_css() -> None:
    st.markdown(
        """
        <style>
        :root {
            --ink: #0f1f17;
            --muted: #526359;
            --line: #dbe7df;
            --surface: #ffffff;
            --primary: #107c41;
            --primary-light: #e8f5ed;
            --danger: #c0392b;
            --danger-light: #fbeee8;
            --card-shadow: 0 2px 10px rgba(16, 124, 65, 0.05);
        }

        /* Fluid full-width mobile container */
        .stApp {
            background-color: #f7faf8;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }

        [data-testid="stMainBlockContainer"] {
            padding-top: 1rem !important;
            padding-bottom: 5rem !important;
            max-width: 900px !important;
            margin: 0 auto;
        }

        /* Touch-friendly interactive buttons */
        button[kind="primary"], button[kind="secondary"], .stButton > button {
            border-radius: 12px !important;
            min-height: 44px !important;
            font-weight: 600 !important;
            letter-spacing: 0.01em;
            transition: transform 0.1s ease, box-shadow 0.1s ease;
        }

        button:active {
            transform: scale(0.98);
        }

        /* Mobile Segmented Control Nav */
        [data-testid="stSegmentedControl"] {
            position: sticky;
            top: 0.25rem;
            z-index: 100;
            overflow-x: auto;
            white-space: nowrap;
            padding: 0.35rem 0 0.5rem;
            background: #f7faf8;
            border-bottom: 1px solid var(--line);
        }

        [data-testid="stSegmentedControl"] button {
            color: var(--ink) !important;
            background: transparent !important;
            min-height: 42px !important;
        }

        [data-testid="stSegmentedControl"] button[aria-pressed="true"] {
            color: #ffffff !important;
            background: var(--primary) !important;
        }

        /* Metric cards styling */
        [data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid var(--line);
            border-radius: 14px;
            padding: 0.85rem 1rem;
            box-shadow: var(--card-shadow);
        }

        [data-testid="stMetricLabel"] p {
            font-size: 0.82rem !important;
            color: var(--muted) !important;
            font-weight: 600 !important;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        [data-testid="stMetricValue"] {
            font-weight: 700 !important;
            font-size: 1.45rem !important;
            color: var(--ink) !important;
        }

        /* Custom mobile cards */
        .mobile-card {
            background: #ffffff;
            border: 1px solid var(--line);
            border-radius: 14px;
            padding: 1rem;
            margin-bottom: 0.75rem;
            box-shadow: var(--card-shadow);
        }

        .budget-banner {
            background: linear-gradient(135deg, #107c41, #189e54);
            color: white;
            border-radius: 14px;
            padding: 1.1rem;
            margin-bottom: 1.2rem;
            box-shadow: 0 4px 16px rgba(16, 124, 65, 0.2);
        }

        .budget-banner h3 {
            color: white !important;
            margin: 0 0 0.3rem 0;
            font-size: 1.15rem;
        }

        .badge-income {
            background: #e6f7ed;
            color: #107c41;
            padding: 3px 8px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.8rem;
        }

        .badge-expense {
            background: #fdeeee;
            color: #d9383a;
            padding: 3px 8px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.8rem;
        }

        .tx-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0.75rem 0.5rem;
            border-bottom: 1px solid #edf3ef;
        }

        .tx-item:last-child {
            border-bottom: none;
        }

        .tx-left {
            display: flex;
            align-items: center;
            gap: 0.85rem;
        }

        .tx-icon {
            width: 40px;
            height: 40px;
            border-radius: 10px;
            background: #f0f7f2;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.2rem;
        }

        .tx-title {
            font-weight: 600;
            font-size: 0.95rem;
            color: #14241b;
            margin-bottom: 2px;
        }

        .tx-sub {
            font-size: 0.78rem;
            color: #6a7c71;
        }

        .tx-amount {
            text-align: right;
            font-weight: 700;
            font-size: 0.98rem;
        }

        /* Hide Streamlit footer branding */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}

        @media (max-width: 640px) {
            [data-testid="stMainBlockContainer"] {
                padding-left: 0.75rem !important;
                padding-right: 0.75rem !important;
            }
            [data-testid="stMetricValue"] {
                font-size: 1.25rem !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ============================================================================
# OVERVIEW VIEW (Mobile-Optimized Dashboard)
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

    # Calculate active days elapsed for daily spend pace
    if active_range:
        today = date.today()
        period_start, period_end = active_range
        eff_end = min(today, period_end)
        days_count = max(1, (eff_end - period_start).days + 1)
    else:
        days_count = max(1, (visible["date"].max() - visible["date"].min()).days + 1) if not visible.empty else 1

    daily_avg_spend = expense / days_count

    # Header section
    st.markdown(
        f"""
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 0.6rem;">
            <div>
                <h2 style="margin:0; font-size:1.45rem; font-weight:700;">Financial Overview</h2>
                <div style="font-size:0.85rem; color:#5c6e64;">📅 {period_name}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # --- KPI Grid (2x2 on mobile) ---
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

    # --- Monthly Budget Card ---
    monthly_budget = convert_usd_budget(float(settings.get("monthly_budget_usd", 1000.0)), currency, exchange_rate)
    if monthly_budget > 0:
        # Calculate spending in current month for the budget tracker
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
        pct_used = min(100.0, (curr_month_expense / monthly_budget) * 100)
        remaining_budget = monthly_budget - curr_month_expense
        days_left = max(1, days_in_month - today.day + 1)
        daily_allowance = max(0.0, remaining_budget / days_left)

        status_text = "On track" if pct_used < 75 else ("Approaching limit" if pct_used < 100 else "Over budget!")
        status_color = "#4ade80" if pct_used < 75 else ("#facc15" if pct_used < 100 else "#f87171")

        st.markdown(
            f"""
            <div class="budget-banner">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <div style="font-size:0.75rem; text-transform:uppercase; letter-spacing:0.06em; opacity:0.85;">This Month's Budget</div>
                        <h3 style="font-size:1.35rem; margin:0.2rem 0;">{format_currency(curr_month_expense, currency)} <span style="font-size:0.9rem; font-weight:normal; opacity:0.9;">of {format_currency(monthly_budget, currency)}</span></h3>
                    </div>
                    <div style="text-align:right;">
                        <span style="background:rgba(255,255,255,0.22); padding:4px 10px; border-radius:20px; font-size:0.8rem; font-weight:700; color:{status_color};">
                            {status_text}
                        </span>
                        <div style="font-size:0.85rem; margin-top:0.3rem;">{pct_used:.0f}% used</div>
                    </div>
                </div>
                <div style="margin-top:0.7rem; background:rgba(255,255,255,0.25); border-radius:10px; height:8px; overflow:hidden;">
                    <div style="width:{min(100, pct_used)}%; background:{status_color}; height:100%; border-radius:10px;"></div>
                </div>
                <div style="display:flex; justify-content:space-between; margin-top:0.6rem; font-size:0.82rem; opacity:0.92;">
                    <span>Remaining: <strong>{format_currency(remaining_budget, currency)}</strong></span>
                    <span>Daily allowance: <strong>{format_currency(daily_allowance, currency)}/day</strong> ({days_left}d left)</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # --- Quick Cash Flow Chart ---
    if not visible.empty:
        with st.container(border=True):
            st.markdown("<div style='font-weight:600; font-size:1rem; margin-bottom:0.2rem;'>Cash Flow Trend</div>", unsafe_allow_html=True)
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
                marker_color="#107c41",
            ))
            fig.add_trace(go.Bar(
                x=daily_flow["date"],
                y=daily_flow["Expense"],
                name="Expense",
                marker_color="#d9383a",
            ))
            fig.update_layout(
                barmode="group",
                margin=dict(l=10, r=10, t=15, b=10),
                height=240,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                yaxis=dict(showgrid=True, gridcolor="#edf3ef", tickprefix=currency),
                xaxis=dict(showgrid=False),
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # --- Spending Snapshot & Recent Transactions ---
    col_chart, col_recent = st.columns([1, 1])

    with col_chart:
        with st.container(border=True):
            st.markdown("<div style='font-weight:600; font-size:1rem; margin-bottom:0.5rem;'>Top Spending Categories</div>", unsafe_allow_html=True)
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
                    hole=0.55,
                    color_discrete_sequence=px.colors.qualitative.Prism,
                )
                fig_cat.update_traces(textposition="inside", textinfo="percent+label")
                fig_cat.update_layout(
                    margin=dict(l=10, r=10, t=10, b=10),
                    height=240,
                    showlegend=False,
                    paper_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig_cat, use_container_width=True, config={"displayModeBar": False})

    with col_recent:
        with st.container(border=True):
            st.markdown("<div style='font-weight:600; font-size:1rem; margin-bottom:0.5rem;'>Recent Activity</div>", unsafe_allow_html=True)
            if visible.empty:
                st.caption("No transactions found. Tap 'Quick Add' to record one!")
            else:
                latest = visible.sort_values(["date", "created_at"], ascending=False).head(5)
                for _, row in latest.iterrows():
                    is_inc = row["type"] == "Income"
                    sign = "+" if is_inc else "-"
                    amt_color = "#107c41" if is_inc else "#d9383a"
                    icon = get_category_icon(str(row["category"]))
                    badge_class = "badge-income" if is_inc else "badge-expense"
                    formatted_dt = row["date"].strftime("%d %b") if pd.notna(row["date"]) else ""

                    st.markdown(
                        f"""
                        <div class="tx-item">
                            <div class="tx-left">
                                <div class="tx-icon">{icon}</div>
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
# QUICK ADD VIEW (Ergonomic Touch Inputs)
# ============================================================================

def render_quick_add(currency: str) -> None:
    st.markdown(
        """
        <div>
            <h2 style="margin:0 0 0.2rem 0; font-size:1.45rem; font-weight:700;">Record Transaction</h2>
            <div style="font-size:0.85rem; color:#5c6e64; margin-bottom: 1rem;">Log spending or earnings in seconds.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if "add_amount" not in st.session_state:
        st.session_state.add_amount = 0.0

    # Type Selector
    tx_type = st.segmented_control(
        "Type",
        options=["Expense", "Income"],
        default="Expense",
        key="quick_add_type",
        label_visibility="collapsed",
    )

    with st.container(border=True):
        # Quick Amount Chips (for quick thumb tap)
        st.caption("Quick increment (+):")
        q_cols = st.columns(5)
        chips = [5.0, 10.0, 25.0, 50.0, 100.0]
        for i, val in enumerate(chips):
            with q_cols[i]:
                if st.button(f"+{int(val)}", key=f"chip_{val}", use_container_width=True):
                    st.session_state.add_amount = round(st.session_state.add_amount + val, 2)
                    st.rerun()

        # Main Amount Input
        amount = st.number_input(
            "Amount (ETB)",
            min_value=0.01,
            value=max(0.01, float(st.session_state.add_amount)) if st.session_state.add_amount > 0 else 10.00,
            step=1.0,
            format="%.2f",
            key="input_amount",
        )

        # Date Picker (Defaults to today)
        tx_date = st.date_input("Transaction Date", value=date.today(), key="input_date")

        # Category & Recipient/Source
        if tx_type == "Expense":
            cat_options = [f"{get_category_icon(c)} {c}" for c in EXPENSE_CATEGORIES]
            selected_cat_raw = st.selectbox("Category", options=cat_options, index=0)
            clean_category = selected_cat_raw.split(" ", 1)[1] if " " in selected_cat_raw else selected_cat_raw

            detail = st.selectbox("Paid For (Beneficiary)", options=EXPENSE_RECIPIENTS, index=0)
        else:
            clean_category = "Income"
            detail = st.selectbox("Income Source", options=INCOME_SOURCES, index=0)

        # Payment Method
        pay_options = [f"{get_payment_icon(p)} {p}" for p in PAYMENT_METHODS]
        selected_pay_raw = st.selectbox("Payment Method", options=pay_options, index=0)
        clean_payment = selected_pay_raw.split(" ", 1)[1] if " " in selected_pay_raw else selected_pay_raw

        # Optional Notes
        notes = st.text_input("Notes (Optional)", placeholder="e.g. Lunch with team, monthly electric bill...")

        st.space("small")
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
                st.toast(f"{tx_type} saved: {format_currency(float(amount), 'ETB')}", icon="✅")
                # Switch to Overview view
                st.session_state.app_view = "📊 Overview"
                st.rerun()


# ============================================================================
# ANALYTICS VIEW
# ============================================================================

def render_analytics(transactions: pd.DataFrame, currency: str, exchange_rate: float) -> None:
    st.markdown(
        """
        <div>
            <h2 style="margin:0 0 0.2rem 0; font-size:1.45rem; font-weight:700;">Deep Spending Analytics</h2>
            <div style="font-size:0.85rem; color:#5c6e64; margin-bottom: 0.8rem;">Break down where your money flows.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if transactions.empty:
        st.info("No transaction data available. Add transactions to see analytics.")
        return

    # Filter by period inside analytics
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

    # Category Breakdown Chart
    with st.container(border=True):
        st.markdown("<div style='font-weight:600; font-size:1.05rem;'>Category Breakdown & Share</div>", unsafe_allow_html=True)
        if not expenses.empty:
            cat_df = expenses.groupby("category")["amount"].sum().reset_index().sort_values("amount", ascending=False)
            cat_df["share"] = (cat_df["amount"] / total_expense) * 100

            fig_donut = px.pie(
                cat_df,
                values="amount",
                names="category",
                hole=0.6,
                color_discrete_sequence=px.colors.qualitative.Safe,
            )
            fig_donut.update_traces(textposition="outside", textinfo="percent+label")
            fig_donut.update_layout(
                margin=dict(l=10, r=10, t=10, b=10),
                height=280,
                showlegend=False,
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_donut, use_container_width=True, config={"displayModeBar": False})

            # Ranked list
            st.markdown("<div style='font-size:0.85rem; font-weight:600; color:#526359; margin-top:0.4rem;'>Ranked Spending:</div>", unsafe_allow_html=True)
            for _, r in cat_df.iterrows():
                icon = get_category_icon(r["category"])
                st.markdown(
                    f"""
                    <div style="display:flex; justify-content:space-between; padding:4px 0; font-size:0.88rem; border-bottom:1px solid #f0f4f1;">
                        <span>{icon} {r['category']}</span>
                        <span><strong>{format_currency(r['amount'], currency)}</strong> <span style="color:#718277; font-size:0.8rem;">({r['share']:.1f}%)</span></span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.caption("No expenses recorded in this period.")

    # Payment Methods & Recipients breakdown
    col1, col2 = st.columns(2)

    with col1:
        with st.container(border=True):
            st.markdown("<div style='font-weight:600; font-size:1rem; margin-bottom:0.4rem;'>Payment Channels</div>", unsafe_allow_html=True)
            if not expenses.empty:
                pay_df = expenses.groupby("payment_method")["amount"].sum().reset_index().sort_values("amount", ascending=False)
                fig_pay = px.bar(
                    pay_df,
                    x="payment_method",
                    y="amount",
                    color="payment_method",
                    color_discrete_sequence=px.colors.qualitative.Pastel,
                )
                fig_pay.update_layout(
                    margin=dict(l=5, r=5, t=10, b=5),
                    height=200,
                    showlegend=False,
                    xaxis_title=None,
                    yaxis_title=None,
                    yaxis=dict(tickprefix=currency, showgrid=True, gridcolor="#edf3ef"),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig_pay, use_container_width=True, config={"displayModeBar": False})

    with col2:
        with st.container(border=True):
            st.markdown("<div style='font-weight:600; font-size:1rem; margin-bottom:0.4rem;'>Beneficiaries (Paid For)</div>", unsafe_allow_html=True)
            if not expenses.empty:
                rec_df = expenses.groupby("detail")["amount"].sum().reset_index().sort_values("amount", ascending=False)
                fig_rec = px.bar(
                    rec_df,
                    x="amount",
                    y="detail",
                    orientation="h",
                    color_discrete_sequence=["#107c41"],
                )
                fig_rec.update_layout(
                    margin=dict(l=5, r=5, t=10, b=5),
                    height=200,
                    showlegend=False,
                    xaxis_title=None,
                    yaxis_title=None,
                    xaxis=dict(tickprefix=currency, showgrid=True, gridcolor="#edf3ef"),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig_rec, use_container_width=True, config={"displayModeBar": False})

    # Top 5 Largest Expenses
    if not expenses.empty:
        with st.container(border=True):
            st.markdown("<div style='font-weight:600; font-size:1.05rem; margin-bottom:0.4rem;'>🔥 Largest Purchases in Period</div>", unsafe_allow_html=True)
            top_expenses = expenses.sort_values("amount", ascending=False).head(5)
            for _, row in top_expenses.iterrows():
                icon = get_category_icon(str(row["category"]))
                note_str = f" • {row['notes']}" if row['notes'] else ""
                st.markdown(
                    f"""
                    <div class="tx-item">
                        <div class="tx-left">
                            <div class="tx-icon">{icon}</div>
                            <div>
                                <div class="tx-title">{row['category']}</div>
                                <div class="tx-sub">{row['date'].strftime('%d %b %Y')} • {row['detail']}{note_str}</div>
                            </div>
                        </div>
                        <div class="tx-amount" style="color:#d9383a;">
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
            <h2 style="margin:0 0 0.2rem 0; font-size:1.45rem; font-weight:700;">Budget & Goals</h2>
            <div style="font-size:0.85rem; color:#5c6e64; margin-bottom: 0.8rem;">Set your monthly spending target and stay disciplined.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    curr_budget_usd = float(settings.get("monthly_budget_usd", 1000.0))
    curr_budget = convert_usd_budget(curr_budget_usd, currency, exchange_rate)

    with st.container(border=True):
        st.markdown("<div style='font-weight:600; font-size:1.05rem; margin-bottom:0.5rem;'>Monthly Spending Target</div>", unsafe_allow_html=True)
        new_budget = st.number_input(
            f"Set Monthly Budget Limit ({CURRENCY_SYMBOLS[currency]})",
            min_value=0.0,
            value=curr_budget,
            step=50.0,
            format="%.2f",
        )
        if st.button("Save Budget Target", type="primary", use_container_width=True):
            settings["monthly_budget_usd"] = (
                float(new_budget) / exchange_rate if currency == "ETB" else float(new_budget)
            )
            save_user_settings(settings)
            st.toast("Budget target updated successfully!", icon="🎯")
            st.rerun()

    # Budget Calculation
    today = date.today()
    start_of_month = today.replace(day=1)
    _, days_in_month = calendar.monthrange(today.year, today.month)
    end_of_month = today.replace(day=days_in_month)

    month_tx = filtered_transactions(transactions, (start_of_month, end_of_month))
    month_tx = display_transactions(month_tx, currency, exchange_rate)
    month_expense = month_tx.loc[month_tx["type"] == "Expense", "amount"].sum()
    month_income = month_tx.loc[month_tx["type"] == "Income", "amount"].sum()

    days_passed = today.day
    days_remaining = max(1, days_in_month - days_passed + 1)
    pct_spent = (month_expense / new_budget * 100) if new_budget > 0 else 0
    remaining_balance = new_budget - month_expense
    daily_budget = max(0.0, remaining_balance / days_remaining)

    with st.container(border=True):
        st.markdown("<div style='font-weight:600; font-size:1.05rem; margin-bottom:0.5rem;'>Current Month Performance</div>", unsafe_allow_html=True)
        b_col1, b_col2 = st.columns(2)
        with b_col1:
            st.metric("Spent So Far", format_currency(month_expense, currency), delta=f"{pct_spent:.0f}% of budget", delta_color="inverse")
        with b_col2:
            st.metric("Remaining Budget", format_currency(remaining_balance, currency), delta=f"{days_remaining} days left")

        # Progress bar
        prog_val = min(1.0, max(0.0, pct_spent / 100.0))
        st.progress(prog_val)

        if pct_spent >= 100:
            st.error(f"🚨 You have exceeded your monthly budget by {format_currency(abs(remaining_balance), currency)}!")
        elif pct_spent >= 80:
            st.warning(f"⚠️ Caution: You've used {pct_spent:.0f}% of your budget. Spend cautiously for the rest of the month.")
        else:
            st.success(f"✅ Great job! You are pacing well with {format_currency(daily_budget, currency)}/day remaining.")

    # Category-specific spending breakdown for this month
    with st.container(border=True):
        st.markdown("<div style='font-weight:600; font-size:1.05rem; margin-bottom:0.5rem;'>Spending by Category This Month</div>", unsafe_allow_html=True)
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
            for _, r in cat_month.iterrows():
                icon = get_category_icon(r["category"])
                cat_share = (r["amount"] / month_expense * 100) if month_expense > 0 else 0
                st.markdown(
                    f"""
                    <div style="display:flex; justify-content:space-between; margin-bottom:0.35rem; font-size:0.9rem;">
                        <span>{icon} {r['category']}</span>
                        <span><strong>{format_currency(r['amount'], currency)}</strong> <span style="font-size:0.8rem; color:#607367;">({cat_share:.0f}%)</span></span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


# ============================================================================
# LEDGER VIEW (Search, Filter, Edit, Delete, Export)
# ============================================================================

def render_ledger(transactions: pd.DataFrame, currency: str, exchange_rate: float) -> None:
    st.markdown(
        """
        <div>
            <h2 style="margin:0 0 0.2rem 0; font-size:1.45rem; font-weight:700;">Transaction Ledger</h2>
            <div style="font-size:0.85rem; color:#5c6e64; margin-bottom: 0.8rem;">Review, edit, delete, or export your financial records.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Search and Filter Section
    with st.container(border=True):
        search = st.text_input("🔍 Search entries", placeholder="Search by note, category, recipient, method...", key="ledger_search")

        f_col1, f_col2 = st.columns(2)
        with f_col1:
            type_filter = st.selectbox("Type", options=["All", "Expense", "Income"], key="filter_type")
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

    st.markdown(
        f"<div style='font-size:0.85rem; color:#526359; margin: 0.5rem 0;'>Showing <strong>{len(visible)}</strong> of {len(transactions)} total entries</div>",
        unsafe_allow_html=True,
    )

    if visible.empty:
        st.info("No matching transactions found.")
    else:
        # Display Transaction Cards with Edit / Delete actions
        sorted_visible = display_visible.sort_values(["date", "created_at"], ascending=False)
        for _, row in sorted_visible.iterrows():
            tx_id = str(row["id"])
            is_income = row["type"] == "Income"
            amt_color = "#107c41" if is_income else "#d9383a"
            sign = "+" if is_income else "-"
            icon = get_category_icon(str(row["category"]))
            formatted_date = row["date"].strftime("%d %b %Y") if pd.notna(row["date"]) else ""

            with st.container(border=True):
                col_left, col_right = st.columns([3, 1])
                with col_left:
                    st.markdown(
                        f"""
                        <div style="display:flex; align-items:center; gap:0.6rem;">
                            <span style="font-size:1.3rem;">{icon}</span>
                            <div>
                                <strong style="font-size:0.98rem;">{row['category']}</strong>
                                <span style="font-size:0.75rem; background:{'#e6f7ed' if is_income else '#fdeeee'}; color:{'#107c41' if is_income else '#d9383a'}; padding:2px 6px; border-radius:6px; margin-left:4px;">{row['type']}</span>
                                <div style="font-size:0.8rem; color:#5c6e64; margin-top:2px;">
                                    {formatted_date} • {row['detail']} • {row['payment_method']}
                                </div>
                                {f'<div style="font-size:0.8rem; color:#354d3f; font-style:italic; margin-top:2px;">"{row["notes"]}"</div>' if row['notes'] else ''}
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with col_right:
                    st.markdown(
                        f"""
                        <div style="text-align:right; font-size:1.05rem; font-weight:700; color:{amt_color}; margin-top:4px;">
                            {sign}{format_currency(row['amount'], currency)}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                # Edit and Delete Accordion
                with st.expander("⚙️ Edit / Delete", expanded=False):
                    with st.form(f"edit_form_{tx_id}"):
                        e_type = st.selectbox("Type", ["Expense", "Income"], index=0 if row["type"] == "Expense" else 1, key=f"e_type_{tx_id}")
                        raw_amount = float(transactions.loc[transactions["id"] == tx_id, "amount"].iloc[0])
                        e_amount = st.number_input("Amount (ETB)", min_value=0.01, value=raw_amount, step=1.0, format="%.2f", key=f"e_amt_{tx_id}")
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

    # Export & Backup Center
    st.space("small")
    with st.container(border=True):
        st.markdown("<div style='font-weight:600; font-size:1.05rem; margin-bottom:0.5rem;'>Export & Data Management</div>", unsafe_allow_html=True)
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
    inject_mobile_css()

    if not require_authentication():
        return

    # Load persistent data & settings
    transactions = load_transactions()
    settings = load_user_settings()

    # Session currency setup. Transaction amounts remain stored in ETB.
    if "currency" not in st.session_state:
        st.session_state.currency = settings.get("display_currency", "ETB")

    currency = st.session_state.currency
    exchange_rate, rate_is_live = fetch_usd_to_etb_rate(settings)

    # Sidebar for Settings & Management
    with st.sidebar:
        st.markdown("### 💳 Ledgerly")
        st.caption("Personal Finance & Expense Tracker")

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

        st.caption(f"{len(transactions)} transactions safely recorded.")
        rate_label = "live Google Finance rate" if rate_is_live else "cached exchange rate"
        st.caption(f"1 USD = {exchange_rate:,.2f} ETB ({rate_label})")

        if st.button("Lock Ledger", icon=":material/lock:", use_container_width=True):
            st.session_state.authenticated = False
            st.rerun()

    # Top Mobile Navigation Bar (Quick & Touch-Friendly)
    nav_options = ["📊 Overview", "➕ Quick Add", "📈 Analytics", "🎯 Budget", "📑 Ledger"]
    if "app_view" not in st.session_state:
        st.session_state.app_view = "📊 Overview"

    active_view = st.segmented_control(
        "Navigation",
        options=nav_options,
        default=st.session_state.app_view,
        key="main_nav_segmented",
        label_visibility="collapsed",
    )
    if active_view and active_view != st.session_state.app_view:
        st.session_state.app_view = active_view

    current_view = st.session_state.app_view

    # Top Quick Filter Bar for Overview
    date_preset = "This Month"
    custom_dates = None
    if current_view == "📊 Overview":
        f_col1, f_col2 = st.columns([1.5, 1])
        with f_col1:
            date_preset = st.selectbox(
                "Period",
                options=["This Month", "Last Month", "Last 30 Days", "This Year", "All Time", "Custom"],
                index=0,
                label_visibility="collapsed",
                key="top_date_filter",
            )
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
