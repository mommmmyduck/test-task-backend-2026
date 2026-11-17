from collections import defaultdict
import unittest
from unittest.mock import patch

import main
from amocrm import Customer
from models import AbstractCustomer


class FakeCrmEntity:
    def __init__(self, entity_id, login=None, name="CRM entity", status_id=0,
                 contacts=None, companies=None):
        self.id = entity_id
        self.name = name
        self.status_id = status_id
        self.custom_fields_values = {
            "Логин": {"value": login},
        }
        self.contacts = contacts or []
        self.companies = companies or []

    def get_field(self, field_name):
        return self.custom_fields_values.get(field_name, {}).get("value")

    def as_dict(self):
        return {"id": self.id, "name": self.name}


def stored_entity(entity_id, login=None, name="Stored entity"):
    return {
        "general": {"id": entity_id, "login": login, "name": name},
        "api_value": {"id": entity_id},
    }


class FakeResult:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.added = []
        self.deleted = []
        self.commits = 0
        self.rollbacks = 0

    def scalars(self, _statement):
        del _statement
        return FakeResult(self.rows)

    def add(self, row):
        self.added.append(row)

    def delete(self, row):
        self.deleted.append(row)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class CollectLeadsCustomersTests(unittest.TestCase):
    def run_sync(self, session, customers, leads, is_full=True):
        def crm_get(entity_type, **_kwargs):
            del _kwargs
            return iter(customers if entity_type is Customer else leads)

        with patch.object(main.db, "session", session), patch.object(
            main, "amocrm_get", side_effect=crm_get
        ):
            main.collect_leads_customers(is_full=is_full)

    def test_lead_id_wins_and_distinct_login_row_is_merged(self):
        lead_row = AbstractCustomer(
            amo_leads=[stored_entity(101, "account")],
            amo_customers=[],
            amo_contacts=[stored_entity(301), stored_entity(302)],
            amo_companies=[],
            send_methods=[
                {"type": "mail", "value": "a@example.com", "desc": "target"},
                {"type": "sms", "value": "+100", "desc": "same"},
            ],
        )
        customer_row = AbstractCustomer(
            amo_leads=[],
            amo_customers=[stored_entity(202, "account")],
            amo_contacts=[stored_entity(400)],
            amo_companies=[],
            send_methods=[
                {"type": "mail", "value": "a@example.com", "desc": "source"},
                {"type": "wtsp", "value": "+200", "desc": "source only"},
            ],
        )
        source_send_methods = [method.copy() for method in customer_row.send_methods]
        session = FakeSession([lead_row, customer_row])

        self.run_sync(
            session,
            [FakeCrmEntity(202, "account", contacts=[{"id": 400}])],
            [FakeCrmEntity(101, "account", contacts=[{"id": 301}])],
        )

        self.assertIn(customer_row, session.deleted)
        self.assertEqual(
            {item["general"]["id"] for item in lead_row.amo_customers},
            {202},
        )
        self.assertEqual(
            {item["general"]["id"] for item in lead_row.amo_leads},
            {101},
        )
        self.assertEqual(
            {item["general"]["id"] for item in lead_row.amo_contacts},
            {301, 400},
        )
        self.assertEqual(
            lead_row.send_methods,
            [
                {"type": "mail", "value": "a@example.com", "desc": "target"},
                {"type": "sms", "value": "+100", "desc": "same"},
                {"type": "wtsp", "value": "+200", "desc": "source only"},
            ],
        )
        self.assertEqual(customer_row.send_methods, source_send_methods)
        self.assertEqual(session.commits, 1)

    def test_merge_send_methods_treats_none_as_empty(self):
        target = AbstractCustomer(
            amo_customers=[],
            amo_leads=[],
            amo_contacts=[],
            amo_companies=[],
            send_methods=None,
        )
        source = AbstractCustomer(
            amo_customers=[],
            amo_leads=[],
            amo_contacts=[],
            amo_companies=[],
            send_methods=[
                {"type": "mail", "value": "a@example.com", "desc": "email"},
            ],
        )
        source_send_methods = source.send_methods
        session = FakeSession([target, source])
        rows = {id(target): target, id(source): source}

        main._merge_rows(
            target,
            source,
            session,
            rows,
            seen={},
            id_index=defaultdict(lambda: defaultdict(set)),
            login_index=defaultdict(lambda: defaultdict(set)),
            login_counts={},
        )

        self.assertEqual(
            target.send_methods,
            [{"type": "mail", "value": "a@example.com", "desc": "email"}],
        )
        self.assertIs(source.send_methods, source_send_methods)
        self.assertIsNot(target.send_methods, source_send_methods)

    def test_customer_falls_back_to_saved_lead_login_and_skips_empty_login(self):
        lead_row = AbstractCustomer(
            amo_leads=[stored_entity(101, "account")],
            amo_customers=[],
            amo_contacts=[],
            amo_companies=[],
        )
        session = FakeSession([lead_row])

        self.run_sync(
            session,
            [
                FakeCrmEntity(202, "account"),
                FakeCrmEntity(203, None),
            ],
            [],
            is_full=False,
        )

        self.assertEqual(
            {item["general"]["id"] for item in lead_row.amo_customers},
            {202},
        )
        self.assertEqual(session.added, [])

    def test_partial_sync_does_not_clean_full_snapshot(self):
        session = FakeSession([])

        with patch.object(main, "_clean_full_snapshot") as cleanup:
            self.run_sync(
                session,
                [FakeCrmEntity(202, "customer-account")],
                [FakeCrmEntity(101, "lead-account")],
                is_full=False,
            )

        cleanup.assert_not_called()
        self.assertEqual(session.commits, 1)
        self.assertEqual(session.rollbacks, 0)

    def test_ambiguous_login_does_not_associate_lead(self):
        rows = [
            AbstractCustomer(
                amo_customers=[stored_entity(entity_id, "duplicate")],
                amo_leads=[],
                amo_contacts=[],
                amo_companies=[],
            )
            for entity_id in (1, 2)
        ]
        session = FakeSession(rows)

        self.run_sync(
            session, [], [FakeCrmEntity(99, "duplicate")], is_full=False
        )

        self.assertEqual(len(session.added), 1)
        self.assertEqual(session.added[0].amo_leads[0]["general"]["id"], 99)
        self.assertEqual(session.commits, 1)

    def test_interrupted_full_sync_rolls_back_without_cleanup(self):
        stale_row = AbstractCustomer(
            amo_customers=[stored_entity(1, "old")],
            amo_leads=[],
            amo_contacts=[],
            amo_companies=[],
        )
        session = FakeSession([stale_row])

        def broken_leads():
            yield FakeCrmEntity(50, "new")
            raise RuntimeError("CRM stream interrupted")

        def crm_get(entity_type, **_kwargs):
            del _kwargs
            if entity_type is Customer:
                return iter([])
            return broken_leads()

        with patch.object(main.db, "session", session), patch.object(
            main, "amocrm_get", side_effect=crm_get
        ):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                main.collect_leads_customers(is_full=True)

        self.assertEqual(session.deleted, [])
        self.assertEqual(session.commits, 0)
        self.assertEqual(session.rollbacks, 1)

    def test_full_snapshot_removes_stale_and_delete_named_entities(self):
        delete_row = AbstractCustomer(
            amo_customers=[stored_entity(1, "marked")],
            amo_leads=[],
            amo_contacts=[],
            amo_companies=[],
        )
        stale_row = AbstractCustomer(
            amo_customers=[stored_entity(2, "stale")],
            amo_leads=[],
            amo_contacts=[],
            amo_companies=[],
        )
        session = FakeSession([delete_row, stale_row])

        self.run_sync(
            session,
            [FakeCrmEntity(1, "marked", name="Удалить из CRM")],
            [],
        )

        self.assertCountEqual(session.deleted, [delete_row, stale_row])
        self.assertEqual(session.commits, 1)

    def test_duplicate_customer_crm_id_aborts_without_full_cleanup(self):
        rows = [
            AbstractCustomer(
                amo_customers=[stored_entity(7, "first")],
                amo_leads=[],
                amo_contacts=[],
                amo_companies=[],
            ),
            AbstractCustomer(
                amo_customers=[stored_entity(7, "second")],
                amo_leads=[],
                amo_contacts=[],
                amo_companies=[],
            ),
        ]
        session = FakeSession(rows)

        with patch.object(main, "_clean_full_snapshot") as cleanup:
            with self.assertLogs("main", level="ERROR") as captured:
                with self.assertRaisesRegex(
                    main.DuplicateCrmEntityIdError, "customer.*7"
                ):
                    self.run_sync(session, [FakeCrmEntity(7, "current")], [])

        self.assertTrue(any("customer ID 7" in line for line in captured.output))
        cleanup.assert_not_called()
        self.assertEqual(session.rollbacks, 1)
        self.assertEqual(session.commits, 0)
        self.assertEqual(session.deleted, [])

    def test_duplicate_lead_crm_id_aborts_without_full_cleanup(self):
        rows = [
            AbstractCustomer(
                amo_customers=[],
                amo_leads=[stored_entity(9, "first")],
                amo_contacts=[],
                amo_companies=[],
            ),
            AbstractCustomer(
                amo_customers=[],
                amo_leads=[stored_entity(9, "second")],
                amo_contacts=[],
                amo_companies=[],
            ),
        ]
        session = FakeSession(rows)

        with patch.object(main, "_clean_full_snapshot") as cleanup:
            with self.assertLogs("main", level="ERROR") as captured:
                with self.assertRaisesRegex(
                    main.DuplicateCrmEntityIdError, "lead.*9"
                ):
                    self.run_sync(session, [], [FakeCrmEntity(9, "current")])

        self.assertTrue(any("lead ID 9" in line for line in captured.output))
        cleanup.assert_not_called()
        self.assertEqual(session.rollbacks, 1)
        self.assertEqual(session.commits, 0)
        self.assertEqual(session.deleted, [])

    def test_unique_crm_ids_keep_existing_update_behavior(self):
        customer_row = AbstractCustomer(
            amo_customers=[stored_entity(17, "customer")],
            amo_leads=[],
            amo_contacts=[],
            amo_companies=[],
        )
        lead_row = AbstractCustomer(
            amo_customers=[],
            amo_leads=[stored_entity(27)],
            amo_contacts=[],
            amo_companies=[],
        )
        session = FakeSession([customer_row, lead_row])

        self.run_sync(
            session,
            [FakeCrmEntity(17, "customer", name="Updated customer")],
            [FakeCrmEntity(27, name="Updated lead")],
        )

        self.assertEqual(customer_row.amo_customers[0]["general"]["name"],
                         "Updated customer")
        self.assertEqual(lead_row.amo_leads[0]["general"]["name"],
                         "Updated lead")
        self.assertEqual(session.deleted, [])
        self.assertEqual(session.commits, 1)
        self.assertEqual(session.rollbacks, 0)


if __name__ == "__main__":
    unittest.main()
