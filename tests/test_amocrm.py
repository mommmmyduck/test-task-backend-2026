import unittest
from types import SimpleNamespace
from unittest.mock import patch

from abstract_customer import get_customer_json, get_lead_json
from amocrm import (
    AmoCrmError,
    AmoCrmNoConten,
    AmoCrmRepeatError,
    Customer,
    Lead,
    _entity_from_api_payload,
    amocrm_get,
)


def customer_payload(entity_id=1):
    return {
        "id": entity_id,
        "name": "Customer",
        "custom_fields_values": [
            {
                "field_id": 10,
                "field_name": "Логин",
                "values": [{"value": "account"}],
            }
        ],
        "_embedded": {
            "contacts": [{"id": 101}],
            "companies": [{"id": 201}],
        },
    }


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload
        self.url = "https://crm.example/api/v4/customers"
        self.request = SimpleNamespace(method="GET")

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class AmoCrmConversionTests(unittest.TestCase):
    def test_customer_payload_normalizes_custom_fields_and_preserves_api_value(self):
        payload = customer_payload()

        customer = _entity_from_api_payload(Customer, payload)

        self.assertEqual(customer.get_field("Логин"), "account")
        self.assertEqual(customer.contacts, [{"id": 101}])
        self.assertEqual(customer.companies, [{"id": 201}])
        self.assertEqual(get_customer_json(customer)["api_value"], payload)

    def test_lead_payload_populates_embedded_entities_and_serializes(self):
        payload = {
            "id": 2,
            "name": "Lead",
            "custom_fields_values": [
                {
                    "field_id": 11,
                    "field_name": "Логин",
                    "values": [{"value": "lead-account"}],
                }
            ],
            "_embedded": {
                "contacts": [{"id": 102}],
                "companies": [{"id": 202}],
                "tags": [{"id": 303}],
            },
        }

        lead = _entity_from_api_payload(Lead, payload)

        self.assertEqual(lead.get_field("Логин"), "lead-account")
        self.assertEqual(lead.contacts, [{"id": 102}])
        self.assertEqual(lead.companies, [{"id": 202}])
        self.assertEqual(lead.tags, [{"id": 303}])
        self.assertEqual(get_lead_json(lead)["api_value"], payload)

    def test_missing_custom_field_name_fails_conversion(self):
        payload = {
            "id": 1,
            "custom_fields_values": [{"field_id": 10, "values": []}],
        }

        with self.assertRaisesRegex(ValueError, "missing field_name"):
            _entity_from_api_payload(Customer, payload)

    def test_http_error_is_not_treated_as_an_empty_result(self):
        with patch("amocrm.requests.get", return_value=FakeResponse(500, {})):
            with self.assertRaises(AmoCrmError) as raised:
                list(amocrm_get(Customer))

        self.assertEqual(raised.exception.error_code, 500)

    def test_invalid_json_is_not_treated_as_an_empty_result(self):
        response = FakeResponse(200, ValueError("invalid JSON"))
        with patch("amocrm.requests.get", return_value=response):
            with self.assertRaisesRegex(AmoCrmError, "invalid JSON"):
                list(amocrm_get(Customer))

    def test_request_failure_is_propagated_as_crm_error(self):
        import requests

        with patch(
                "amocrm.requests.get",
                side_effect=requests.Timeout("request timed out")):
            with self.assertRaisesRegex(AmoCrmError, "request timed out"):
                list(amocrm_get(Customer))

    def test_no_content_is_not_treated_as_successful_end_of_iterator(self):
        with patch("amocrm.requests.get", return_value=FakeResponse(204)):
            with self.assertRaises(AmoCrmNoConten):
                list(amocrm_get(Customer))

    def test_missing_embedded_collection_fails_instead_of_returning_empty(self):
        with patch(
                "amocrm.requests.get",
                return_value=FakeResponse(200, {"_embedded": {}})):
            with self.assertRaisesRegex(AmoCrmError, "_embedded.customers"):
                list(amocrm_get(Customer))

    def test_http_429_retries_then_propagates_error(self):
        with patch("amocrm.time.sleep"), patch(
                "amocrm.requests.get",
                return_value=FakeResponse(429, {})) as get:
            with self.assertRaises(AmoCrmRepeatError):
                list(amocrm_get(Customer))

        self.assertEqual(get.call_count, 4)

    def test_pagination_follows_next_link_and_stops_without_one(self):
        first_page = FakeResponse(
            200,
            {
                "_embedded": {"customers": [customer_payload(1)]},
                "_links": {"next": {"href": "https://crm.example/page/2"}},
            },
        )
        second_page = FakeResponse(
            200,
            {"_embedded": {"customers": [customer_payload(2)]}},
        )
        with patch("amocrm.time.sleep"), patch(
                "amocrm.requests.get",
                side_effect=[first_page, second_page]) as get:
            customers = list(amocrm_get(Customer))

        self.assertEqual([customer.id for customer in customers], [1, 2])
        self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
