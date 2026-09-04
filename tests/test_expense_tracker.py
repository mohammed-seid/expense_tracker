import unittest
import pandas as pd
from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile
import shutil

# We will import helper logic from app
import app


class TestExpenseTracker(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_csv = Path(self.test_dir) / "test_expenses.csv"
        self.test_config = Path(self.test_dir) / "test_settings.json"
        # Monkeypatch DATA_FILE and SETTINGS_FILE
        self.orig_data_file = app.DATA_FILE
        self.orig_settings_file = getattr(app, "SETTINGS_FILE", None)
        app.DATA_FILE = self.test_csv
        app.SETTINGS_FILE = self.test_config

    def tearDown(self):
        app.DATA_FILE = self.orig_data_file
        if self.orig_settings_file:
            app.SETTINGS_FILE = self.orig_settings_file
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_existing_data_file_loading(self):
        # Create CSV with the exact format of the user's existing expenses.csv
        content = (
            "id,date,type,amount,category,detail,payment_method,notes,created_at\n"
            "20260902221557907134,2026-09-02,Expense,0.01,Food & dining,Self,Cash,,2026-09-02T22:15:57\n"
        )
        self.test_csv.write_text(content, encoding="utf-8")

        df = app.load_transactions()
        self.assertEqual(len(df), 1)
        self.assertEqual(str(df.iloc[0]["id"]), "20260902221557907134")
        self.assertEqual(df.iloc[0]["amount"], 0.01)
        self.assertEqual(df.iloc[0]["type"], "Expense")
        self.assertEqual(df.iloc[0]["category"], "Food & dining")
        self.assertEqual(df.iloc[0]["date"], date(2026, 9, 2))

    def test_add_transaction(self):
        app.add_transaction(
            transaction_type="Expense",
            amount=50.0,
            transaction_date=date(2026, 9, 3),
            category="Transport",
            detail="Self",
            payment_method="Mobile money",
            notes="Bus ticket",
        )
        df = app.load_transactions()
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["amount"], 50.0)
        self.assertEqual(df.iloc[0]["notes"], "Bus ticket")
        self.assertTrue(self.test_csv.exists())

    def test_update_transaction(self):
        app.add_transaction(
            transaction_type="Expense",
            amount=50.0,
            transaction_date=date(2026, 9, 3),
            category="Transport",
            detail="Self",
            payment_method="Mobile money",
            notes="Bus ticket",
        )
        df = app.load_transactions()
        tx_id = str(df.iloc[0]["id"])

        success = app.update_transaction(
            transaction_id=tx_id,
            transaction_type="Expense",
            amount=75.0,
            transaction_date=date(2026, 9, 4),
            category="Transport",
            detail="Self",
            payment_method="Debit card",
            notes="Updated ticket",
        )
        self.assertTrue(success)

        updated_df = app.load_transactions()
        self.assertEqual(updated_df.iloc[0]["amount"], 75.0)
        self.assertEqual(updated_df.iloc[0]["date"], date(2026, 9, 4))
        self.assertEqual(updated_df.iloc[0]["payment_method"], "Debit card")
        self.assertEqual(updated_df.iloc[0]["notes"], "Updated ticket")

    def test_delete_transaction(self):
        app.add_transaction(
            transaction_type="Income",
            amount=1000.0,
            transaction_date=date(2026, 9, 1),
            category="Income",
            detail="Salary",
            payment_method="Bank transfer",
            notes="Monthly salary",
        )
        app.add_transaction(
            transaction_type="Expense",
            amount=100.0,
            transaction_date=date(2026, 9, 2),
            category="Food & dining",
            detail="Self",
            payment_method="Cash",
            notes="Groceries",
        )
        df = app.load_transactions()
        self.assertEqual(len(df), 2)

        del_id = str(df.iloc[0]["id"])
        success = app.delete_transaction(del_id)
        self.assertTrue(success)

        remaining_df = app.load_transactions()
        self.assertEqual(len(remaining_df), 1)
        self.assertEqual(remaining_df.iloc[0]["amount"], 100.0)

    def test_filter_presets(self):
        today = date(2026, 9, 4)
        rng_month = app.get_date_preset_range("This Month", today=today)
        self.assertEqual(rng_month[0], date(2026, 9, 1))
        self.assertEqual(rng_month[1], date(2026, 9, 30))

        rng_last_month = app.get_date_preset_range("Last Month", today=today)
        self.assertEqual(rng_last_month[0], date(2026, 8, 1))
        self.assertEqual(rng_last_month[1], date(2026, 8, 31))

        rng_30 = app.get_date_preset_range("Last 30 Days", today=today)
        self.assertEqual(rng_30[0], today - timedelta(days=30))
        self.assertEqual(rng_30[1], today)

    def test_settings_load_save(self):
        settings = app.load_user_settings()
        self.assertIn("monthly_budget", settings)
        self.assertIn("currency", settings)

        settings["monthly_budget"] = 2500.0
        settings["currency"] = "€"
        app.save_user_settings(settings)

        reloaded = app.load_user_settings()
        self.assertEqual(reloaded["monthly_budget"], 2500.0)
        self.assertEqual(reloaded["currency"], "€")


if __name__ == "__main__":
    unittest.main()
