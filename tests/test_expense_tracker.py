import unittest
import pandas as pd
from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile
import shutil
import base64
from unittest.mock import Mock, patch

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
        self.orig_github_storage_config = app.github_storage_config
        app.DATA_FILE = self.test_csv
        app.SETTINGS_FILE = self.test_config
        app.github_storage_config = lambda: None

    def tearDown(self):
        app.DATA_FILE = self.orig_data_file
        if self.orig_settings_file:
            app.SETTINGS_FILE = self.orig_settings_file
        app.github_storage_config = self.orig_github_storage_config
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_github_storage_reads_and_writes_remote_csv(self):
        config = {"token": "test-token", "owner": "test-owner", "repo": "test-repo", "path": "expenses.csv"}
        csv_content = (
            "id,date,type,amount,category,detail,payment_method,notes,created_at\n"
            "123,2026-09-02,Expense,12.5,Food & dining,Self,Cash,Meal,2026-09-02T12:00:00\n"
        )
        get_response = Mock(status_code=200)
        get_response.json.return_value = {
            "sha": "file-sha",
            "content": base64.b64encode(csv_content.encode("utf-8")).decode("ascii"),
        }
        put_response = Mock()

        with patch.object(app, "github_storage_config", return_value=config), \
             patch.object(app.requests, "get", return_value=get_response), \
             patch.object(app.requests, "put", return_value=put_response) as put_request:
            transactions = app.load_transactions()
            transactions.at[0, "notes"] = "Updated remotely"
            app.save_transactions(transactions)

        self.assertEqual(transactions.iloc[0]["amount"], 12.5)
        put_payload = put_request.call_args.kwargs["json"]
        self.assertEqual(put_payload["sha"], "file-sha")
        saved_csv = base64.b64decode(put_payload["content"]).decode("utf-8")
        self.assertIn("Updated remotely", saved_csv)

    def test_github_storage_rejects_stale_csv(self):
        config = {"token": "test-token", "owner": "test-owner", "repo": "test-repo", "path": "expenses.csv"}
        csv_content = "id,date,type,amount,category,detail,payment_method,notes,created_at\n"
        response_before = Mock(status_code=200)
        response_before.json.return_value = {
            "sha": "original-sha",
            "content": base64.b64encode(csv_content.encode("utf-8")).decode("ascii"),
        }
        response_after = Mock(status_code=200)
        response_after.json.return_value = {
            "sha": "newer-sha",
            "content": base64.b64encode(csv_content.encode("utf-8")).decode("ascii"),
        }

        with patch.object(app, "github_storage_config", return_value=config), \
             patch.object(app.requests, "get", side_effect=[response_before, response_after]), \
             patch.object(app.requests, "put") as put_request:
            transactions = app.load_transactions()
            with self.assertRaisesRegex(RuntimeError, "changed since it was loaded"):
                app.save_transactions(transactions)

        put_request.assert_not_called()

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

    def test_unreadable_data_file_does_not_look_empty(self):
        self.test_csv.write_text("not,a,valid,ledger\n\"", encoding="utf-8")

        with self.assertRaises(RuntimeError):
            app.load_transactions()

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
