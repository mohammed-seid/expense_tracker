import streamlit as st
import pandas as pd
import hmac
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Tuple

# ============================================================================
# CONFIGURATION
# ============================================================================

st.set_page_config(
    page_title="Ledgerly | Personal finance",
    page_icon=":material/account_balance_wallet:",
    layout="wide",
    initial_sidebar_state="expanded",
)

DATA_FILE = Path(__file__).with_name("expenses.csv")
TRANSACTION_COLUMNS = [
    "id", "date", "type", "amount", "category", "detail",
    "payment_method", "notes", "created_at",
]
INCOME_SOURCES = ["Salary", "Investment", "Business", "Freelance", "Gift", "Other"]
EXPENSE_RECIPIENTS = ["Self", "Wife / husband", "Family", "Kids", "Friends", "Shared household"]
EXPENSE_CATEGORIES = [
    "Food & dining", "Transport", "Clothes & shoes", "Utilities",
    "Rent / housing", "Health", "Education", "Entertainment",
    "Shopping", "Debt payment", "Gifts", "Other",
]
PAYMENT_METHODS = ["Cash", "Bank transfer", "Debit card", "Credit card", "Mobile money", "Other"]


# ============================================================================
# DATA LAYER
# ============================================================================

def load_transactions() -> pd.DataFrame:
    if not DATA_FILE.exists():
        return pd.DataFrame(columns=TRANSACTION_COLUMNS)
    try:
        transactions = pd.read_csv(DATA_FILE)
    except (OSError, pd.errors.ParserError):
        return pd.DataFrame(columns=TRANSACTION_COLUMNS)
    for column in TRANSACTION_COLUMNS:
        if column not in transactions:
            transactions[column] = ""
    transactions["date"] = pd.to_datetime(transactions["date"], errors="coerce").dt.date
    transactions["amount"] = pd.to_numeric(transactions["amount"], errors="coerce").fillna(0.0)
    return transactions[TRANSACTION_COLUMNS]


def save_transactions(transactions: pd.DataFrame) -> None:
    transactions.to_csv(DATA_FILE, index=False)


def add_transaction(
    transaction_type: str,
    amount: float,
    transaction_date: date,
    category: str,
    detail: str,
    payment_method: str,
    notes: str,
) -> None:
    transactions = load_transactions()
    record = pd.DataFrame([{
        "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "date": transaction_date,
        "type": transaction_type,
        "amount": amount,
        "category": category,
        "detail": detail,
        "payment_method": payment_method,
        "notes": notes.strip(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }])
    save_transactions(pd.concat([transactions, record], ignore_index=True))


# ============================================================================
# HELPERS
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
        st.error(
            "Ledgerly is locked, but no password is configured. Add LEDGERLY_PASSWORD to "
            "Streamlit secrets or your environment before using the app.",
            icon=":material/lock:",
        )
        return False

    if st.session_state.get("authenticated", False):
        return True

    st.markdown(
        '<div class="eyebrow">Private ledger</div>'
        '<h1>Your finances, kept private.</h1>'
        '<p class="hero">Enter your password to open your personal Ledgerly workspace.</p>',
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        with st.form("login_form"):
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button(
                "Unlock ledger", type="primary", icon=":material/lock_open:"
            )
        if submitted:
            if hmac.compare_digest(password, expected_password):
                st.session_state.authenticated = True
                st.rerun()
            st.error("That password did not match.", icon=":material/error:")
    st.caption("Your password is read from private configuration and is never stored in the ledger file.")
    return False

def format_currency(amount: float) -> str:
    return f"${amount:,.2f}"


def filtered_transactions(
    transactions: pd.DataFrame, date_range: Optional[Tuple[date, date]]
) -> pd.DataFrame:
    if not date_range or transactions.empty:
        return transactions
    start, end = date_range
    return transactions[transactions["date"].between(start, end)]


# ============================================================================
# VISUAL SYSTEM
# ============================================================================

def inject_mobile_css() -> None:
    st.markdown(
        """
        <style>
        :root {
            --ink: #17251d;
            --muted: #64746a;
            --line: #d9e6dc;
            --surface: #ffffff;
            --accent: #1a7f37;
            --accent-soft: #e6f3e8;
        }
        .stApp {
            background: radial-gradient(circle at 85% 0%, #eef8ef 0, #f8faf8 32rem, #f8faf8 58rem);
        }
        [data-testid="stMainBlockContainer"] {
            padding-top: 2.6rem;
            padding-bottom: 4rem;
        }
        [data-testid="stSidebar"] {
            box-shadow: 8px 0 28px rgba(15, 50, 27, 0.08);
        }
        [data-testid="stMetric"] {
            background: rgba(255, 255, 255, 0.78);
            border: 1px solid var(--line);
            border-radius: 12px;
            padding: 1rem 1.1rem;
            min-height: 7rem;
        }
        [data-testid="stMetricLabel"] p {
            color: var(--muted);
            font-size: 0.78rem;
            font-weight: 600;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }
        [data-testid="stMetricValue"] {
            color: var(--ink);
            font-family: 'Space Grotesk', sans-serif;
        }
        [data-testid="stVerticalBlockBorderWrapper"] {
            border-color: var(--line);
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.76);
        }
        h1, h2, h3 { color: var(--ink); }
        h1 { letter-spacing: 0; max-width: 44rem; }
        .eyebrow {
            color: var(--accent);
            font-size: 0.78rem;
            font-weight: 700;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            margin-bottom: 0.45rem;
        }
        .hero {
            color: var(--muted);
            font-size: 1.08rem;
            line-height: 1.55;
            max-width: 46rem;
            margin: 0 0 1.6rem;
        }
        .insight-strip {
            display: flex;
            align-items: center;
            gap: 0.7rem;
            color: #35513c;
            background: var(--accent-soft);
            border: 1px solid #cde5d1;
            border-radius: 10px;
            padding: 0.75rem 1rem;
            margin: 0.9rem 0 1.2rem;
            font-size: 0.92rem;
        }
        .insight-strip strong { color: var(--ink); }
        [data-testid="stSidebar"] h2 { letter-spacing: 0; }
        @media (max-width: 768px) {
            [data-testid="stMainBlockContainer"] { padding: 1.2rem 1rem 3rem; }
            .block-container { padding-top: 1rem !important; }
            .hero { font-size: 1rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ============================================================================
# SIDEBAR
# ============================================================================

def render_sidebar(transactions: pd.DataFrame) -> Tuple[str, Optional[Tuple[date, date]]]:
    with st.sidebar:
        st.markdown("## :material/account_balance_wallet: Ledgerly")
        st.caption("A clear view of where your money goes.")
        st.space("small")
        view = st.segmented_control(
            "View",
            ["Overview", "Add transaction", "Transactions"],
            default="Overview",
            key="view",
        )
        st.space("small")
        st.markdown("**Filter dashboard**")
        if transactions.empty:
            date_range = None
        else:
            minimum = transactions["date"].min()
            maximum = transactions["date"].max()
            selected = st.date_input("Date range", value=(minimum, maximum))
            date_range = (
                tuple(selected)
                if isinstance(selected, (tuple, list)) and len(selected) == 2
                else None
            )
        st.space("small")
        st.caption(f"{len(transactions):,} transaction{'s' if len(transactions) != 1 else ''} recorded")
        if st.button("Lock ledger", icon=":material/lock:", width="stretch"):
            st.session_state.authenticated = False
            st.rerun()
    return view or "Overview", date_range


# ============================================================================
# DASHBOARD VIEW
# ============================================================================

def render_dashboard(
    transactions: pd.DataFrame, date_range: Optional[Tuple[date, date]]
) -> None:
    visible = filtered_transactions(transactions, date_range)
    income = visible.loc[visible["type"] == "Income", "amount"].sum()
    expense = visible.loc[visible["type"] == "Expense", "amount"].sum()
    balance = income - expense
    savings_rate = ((balance / income) * 100) if income else 0
    period_label = "All time"
    if date_range:
        period_label = f"{date_range[0].strftime('%d %b %Y')} to {date_range[1].strftime('%d %b %Y')}"

    st.markdown(
        '<div class="eyebrow">Your money, in focus</div>'
        '<h1>Good money habits start with visibility.</h1>'
        '<p class="hero">Track income, understand spending, and make the next decision with confidence.</p>',
        unsafe_allow_html=True,
    )

    st.caption(f":material/calendar_today: Showing {period_label}")

    if visible.empty:
        st.info(
            "Your dashboard is ready. Add your first income or expense to see the story unfold.",
            icon=":material/lightbulb:",
        )

    # --- KPI row ---
    with st.container(horizontal=True):
        st.metric("Balance", format_currency(balance), delta=f"{savings_rate:.0f}% retained")
        st.metric("Income", format_currency(income), delta=f"{len(visible[visible['type'] == 'Income'])} entries")
        st.metric("Expenses", format_currency(expense), delta=f"{len(visible[visible['type'] == 'Expense'])} entries", delta_color="inverse")
        st.metric("Entries", f"{len(visible):,}", delta="In selected period")

    if visible.empty:
        insight = "Start with one entry and Ledgerly will turn it into a useful pattern."
    elif expense == 0:
        insight = "No spending recorded in this period. Your balance is currently fully retained."
    elif savings_rate >= 20:
        insight = f"You retained <strong>{savings_rate:.0f}%</strong> of recorded income in this period."
    else:
        insight = f"Spending used <strong>{(expense / income * 100):.0f}%</strong> of recorded income in this period." if income else "Add income to see how spending compares with it."
    st.markdown(f'<div class="insight-strip"><span class="insight-mark">+</span><span>{insight}</span></div>', unsafe_allow_html=True)

    st.space("small")

    # --- Charts row ---
    chart_left, chart_right = st.columns([1.35, 1])

    with chart_left:
        with st.container(border=True):
            st.subheader("Cash flow over time")
            st.caption("Income and expenses grouped by day")
            if visible.empty:
                st.caption("Add entries to unlock your daily cash-flow trend.")
            else:
                daily = (
                    visible.assign(
                        income=visible["amount"].where(visible["type"] == "Income", 0),
                        expenses=visible["amount"].where(visible["type"] == "Expense", 0),
                    )
                    .groupby("date")[["income", "expenses"]]
                    .sum()
                    .sort_index()
                )
                st.line_chart(daily, y=["income", "expenses"], height=300)

    with chart_right:
        with st.container(border=True):
            st.subheader("Spending by category")
            st.caption("Where your recorded expenses are going")
            spending = (
                visible[visible["type"] == "Expense"]
                .groupby("category")["amount"]
                .sum()
                .sort_values(ascending=False)
            )
            if spending.empty:
                st.caption("Your expense mix will appear here.")
            else:
                st.bar_chart(spending, height=300)

    st.space("small")

    # --- Bottom row ---
    bottom_left, bottom_right = st.columns([1, 1.35])

    with bottom_left:
        with st.container(border=True):
            st.subheader("Who you spend for")
            st.caption("Expense totals by recipient")
            recipients = (
                visible[visible["type"] == "Expense"]
                .groupby("detail")["amount"]
                .sum()
                .sort_values(ascending=False)
            )
            if recipients.empty:
                st.caption("Recipient insights appear after your first expense.")
            else:
                st.dataframe(
                    recipients.rename("Amount").to_frame().style.format("${:,.2f}"),
                    width="stretch",
                    hide_index=False,
                )

    with bottom_right:
        with st.container(border=True):
            st.subheader("Recent activity")
            st.caption("Your latest entries in this period")
            _render_transaction_table(visible.head(8))


# ============================================================================
# ADD TRANSACTION VIEW
# ============================================================================

def render_add_transaction() -> None:
    st.markdown(
        '<div class="eyebrow">New entry</div>'
        '<h1>Add a transaction</h1>'
        '<p class="hero">Capture the detail now, so your future self can see the pattern.</p>',
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        transaction_type = st.segmented_control(
            "This is", ["Expense", "Income"], default="Expense", key="entry_type"
        )

        with st.form("transaction_form", clear_on_submit=True):
            left, right = st.columns(2)

            with left:
                amount = st.number_input(
                    "Amount", min_value=0.01, step=10.0, format="%.2f",
                    help="Enter the full amount.",
                )
                transaction_date = st.date_input("Date", value=date.today())
                payment_method = st.selectbox("Payment method", PAYMENT_METHODS)

            with right:
                if transaction_type == "Income":
                    detail = st.selectbox("Income source", INCOME_SOURCES)
                    category = st.text_input("Category or label", value="Income")
                else:
                    detail = st.selectbox("Paid for", EXPENSE_RECIPIENTS)
                    category = st.selectbox("Expense category", EXPENSE_CATEGORIES)

                notes = st.text_area(
                    "Notes",
                    placeholder="Optional context: monthly bill, school run, project payment...",
                    height=102,
                )

            submitted = st.form_submit_button(
                "Add to ledger", type="primary", icon=":material/add:"
            )

        if submitted:
            add_transaction(
                transaction_type, float(amount), transaction_date,
                category, detail, payment_method, notes,
            )
            st.toast(
                f"{transaction_type} added: {format_currency(float(amount))}",
                icon=":material/check_circle:",
            )
            st.rerun()


# ============================================================================
# TRANSACTIONS TABLE (shared)
# ============================================================================

def _render_transaction_table(transactions: pd.DataFrame) -> None:
    if transactions.empty:
        st.caption("No transactions in this view.")
        return

    display = transactions.sort_values(["date", "created_at"], ascending=False).copy()
    display["date"] = display["date"].apply(
        lambda v: v.strftime("%d %b %Y") if pd.notna(v) else ""
    )
    display["amount"] = display.apply(
        lambda row: ("+" if row["type"] == "Income" else "-") + format_currency(row["amount"]),
        axis=1,
    )
    display = display.rename(columns={
        "date": "Date",
        "type": "Type",
        "amount": "Amount",
        "category": "Category",
        "detail": "For / source",
        "payment_method": "Paid via",
    })
    st.dataframe(
        display[["Date", "Type", "Amount", "Category", "For / source", "Paid via"]],
        width="stretch",
        hide_index=True,
    )


# ============================================================================
# TRANSACTIONS VIEW
# ============================================================================

def render_transactions(transactions: pd.DataFrame) -> None:
    st.markdown(
        '<div class="eyebrow">Ledger</div>'
        '<h1>Every entry, accounted for.</h1>',
        unsafe_allow_html=True,
    )
    st.caption("Search, review, and export the transactions behind your dashboard.")

    search = st.text_input(
        "Search transactions",
        placeholder="Search category, recipient, source, or note",
        icon=":material/search:",
    )

    visible = transactions
    if search:
        text = visible.astype(str).agg(" ".join, axis=1).str.lower()
        visible = visible[text.str.contains(search.lower(), regex=False)]

    _render_transaction_table(visible)

    if not transactions.empty:
        st.download_button(
            "Download CSV",
            transactions.to_csv(index=False),
            "ledgerly-transactions.csv",
            "text/csv",
            icon=":material/download:",
        )


# ============================================================================
# MAIN
# ============================================================================

def main() -> None:
    inject_mobile_css()
    if not require_authentication():
        return
    transactions = load_transactions()
    view, date_range = render_sidebar(transactions)

    if view == "Add transaction":
        render_add_transaction()
    elif view == "Transactions":
        render_transactions(filtered_transactions(transactions, date_range))
    else:
        render_dashboard(transactions, date_range)


if __name__ == "__main__":
    main()
