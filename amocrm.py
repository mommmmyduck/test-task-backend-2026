import time
from typing import Any, Dict, Iterator, Type

import requests

TRANSACTIONS = "transactions"
TASKS = "tasks"
CUSTOMERS = "customers"
COMPANIES = "companies"
CONTACTS = "contacts"
CUSTOM_FIELDS = "custom_fields"
LEADS = "leads"
TAGS = "tags"
PIPELINES = "pipelines"
STATUSES = "statuses"
NOTES = "notes"
EVENTS = "events"


class AmoCrmRepeatError(Exception):
    """Надо ещё раз запрос кинуть"""


class AmoCrmNoConten(Exception):
    """
    amocrm error not found
    """


class AmoCrmSearchError(Exception):
    """
    amocrm error when search
    """


class AmoCrmError(Exception):
    errors = {
        "301": 'Moved permanently',
        "400": 'Bad request',
        "401": 'Unauthorized',
        "403": 'Forbidden',
        "404": 'Not found',
        "500": 'Internal server error',
        "502": 'Bad gateway',
        "503": 'Service unavailable',
        "429": 'Requests Per Second Exceeded'
    }

    def __init__(self, msg=None, error_code=None):
        self.error_code = error_code
        self.error_description = self.errors.get(str(error_code), f'Undescribed error: {error_code}')
        self.msg = f"{msg or 'AmoCRM error'}, {self.error_description}"
        super().__init__(self.msg)


class AmoCrmCompaniesMoreThanOne(Exception):
    """
    amocrm error when found companies > 1
    """


class BaseEntity:
    """
    base class for entity
    """
    schema = None
    entity_name = None
    url = None
    custom_fields_values = None
    PATCH_FIELDS = None

    def as_dict(self):
        if self._api_value is not None:
            return dict(self._api_value)
        result = {
            field: getattr(self, field)
            for field in self.API_FIELDS
        }
        if self._embedded is not None:
            result['_embedded'] = self._embedded
        return result

    def __str__(self):
        return f"<{type(self).__name__}: {self.name}, {self.id}>"

    def __repr__(self):
        return str(self)

    def get_field(self, field_name):
        return self.custom_fields_values.get(field_name, {}).get('value')

    def set_field(self, field_name, field_value, field_id=None):
        custom_field = self.custom_fields_values.get(field_name)
        if custom_field is None:
            if field_id is None:
                raise KeyError(f"Not found custom field with key: {field_name}")
            self.custom_fields_values[field_name] = dict(id=field_id, value=field_value)
            return
        if custom_field.get('id'):
            custom_field['value'] = field_value
        elif field_id:
            custom_field['value'] = field_value
            custom_field['id'] = field_id
        else:
            raise ValueError(f"Custom field {field_name} without id")

    def get_url(self):
        return self.url


class Customer(BaseEntity):
    """
    class for amocrm customer
    """
    url = "api/v4/customers"
    entity_name = CUSTOMERS
    API_FIELDS = (
        "id", "name", "next_price", "next_date", "responsible_user_id",
        "status_id", "periodicity", "created_by", "updated_by", "created_at",
        "updated_at", "closest_task_at", "is_deleted", "ltv",
        "purchases_count", "average_check", "account_id",
        "custom_fields_values",
    )

    def __init__(self,
                 id=None,
                 name=None,
                 next_price=None,
                 next_date=None,
                 responsible_user_id=None,
                 status_id=None,
                 periodicity=None,
                 created_by=None,
                 updated_by=None,
                 created_at=None,
                 updated_at=None,
                 closest_task_at=None,
                 is_deleted=None,
                 ltv=None,
                 purchases_count=None,
                 average_check=None,
                 account_id=None,
                 custom_fields_values=None,
                 _embedded=None
                 ):
        self.id = id
        self.name = name
        self.next_price = next_price
        self.next_date = next_date
        self.responsible_user_id = responsible_user_id
        self.status_id = status_id
        self.periodicity = periodicity
        self.created_by = created_by
        self.updated_by = updated_by
        self.created_at = created_at
        self.updated_at = updated_at
        self.closest_task_at = closest_task_at
        self.is_deleted = is_deleted
        self.ltv = ltv
        self.purchases_count = purchases_count
        self.average_check = average_check
        self.account_id = account_id
        self.custom_fields_values = custom_fields_values or {}
        self._embedded = _embedded
        self._api_value = None

        self.companies = []
        self.contacts = []
        self._new_next_date = None
        if not _embedded:
            return
        if _embedded.get(COMPANIES):
            self.companies = _embedded.get(COMPANIES)
        if _embedded.get(CONTACTS):
            self.contacts = _embedded.get(CONTACTS)


class Lead(BaseEntity):
    """
    class for amocrm lead
    """
    url = "api/v4/leads"
    entity_name = LEADS
    PATCH_FIELDS = ("custom_fields_values", "id")
    API_FIELDS = (
        "name", "id", "price", "responsible_user_id", "group_id", "status_id",
        "pipeline_id", "source_id", "created_by", "updated_by", "created_at",
        "updated_at", "closest_task_at", "is_deleted", "score", "account_id",
        "custom_fields_values",
    )

    def __init__(self,
                 name=None,
                 id=None,
                 price=None,
                 responsible_user_id=None,
                 group_id=None,
                 status_id=None,
                 pipeline_id=None,
                 source_id=None,
                 created_by=None,
                 updated_by=None,
                 created_at=None,
                 updated_at=None,
                 closest_task_at=None,
                 is_deleted=None,
                 score=None,
                 account_id=None,
                 custom_fields_values=None,
                 _embedded=None
                 ):
        self.id = id
        self.name = name
        self.price = price
        self.responsible_user_id = responsible_user_id
        self.group_id = group_id
        self.status_id = status_id
        self.pipeline_id = pipeline_id
        self.source_id = source_id
        self.created_by = created_by
        self.updated_by = updated_by
        self.created_at = created_at
        self.updated_at = updated_at
        self.closest_task_at = closest_task_at
        self.is_deleted = is_deleted
        self.score = score
        self.account_id = account_id
        self.custom_fields_values = custom_fields_values or {}
        self._embedded = _embedded
        self._api_value = None
        self.tags = []
        self.contacts = (_embedded or {}).get(CONTACTS, [])
        self.companies = (_embedded or {}).get(COMPANIES, [])
        self.tags = (_embedded or {}).get(TAGS, [])
        if not _embedded:
            return


def _normalize_custom_fields_values(custom_fields_values):
    if custom_fields_values is None:
        return {}
    if isinstance(custom_fields_values, dict):
        return custom_fields_values
    if not isinstance(custom_fields_values, list):
        raise ValueError("custom_fields_values must be a list or a field-name mapping")

    normalized = {}
    for custom_field in custom_fields_values:
        if not isinstance(custom_field, dict):
            raise ValueError("Each custom field must be an object")
        field_name = custom_field.get("field_name")
        if not field_name:
            raise ValueError("CRM custom field is missing field_name")
        values = custom_field.get("values", [])
        if not isinstance(values, list):
            raise ValueError("CRM custom field values must be a list")
        if field_name in normalized:
            raise ValueError(f"Duplicate CRM custom field name: {field_name}")
        if values and not isinstance(values[0], dict):
            raise ValueError(f"Invalid value for CRM custom field: {field_name}")

        normalized[field_name] = {
            "id": custom_field.get("field_id"),
            "value": values[0].get("value") if values else None,
        }
    return normalized


def _entity_from_api_payload(
        obj_cls: Type[BaseEntity],
        payload: Dict[str, Any],
) -> BaseEntity:
    if not isinstance(payload, dict):
        raise ValueError(f"{obj_cls.__name__} API entity must be an object")
    if payload.get("id") is None:
        raise ValueError(f"{obj_cls.__name__} API entity is missing id")

    embedded = payload.get("_embedded")
    if embedded is not None and not isinstance(embedded, dict):
        raise ValueError(f"{obj_cls.__name__} _embedded must be an object")

    entity_data = {
        field: payload.get(field)
        for field in obj_cls.API_FIELDS
    }
    entity_data["custom_fields_values"] = _normalize_custom_fields_values(
        payload.get("custom_fields_values")
    )
    entity_data["_embedded"] = embedded
    entity = obj_cls(**entity_data)
    entity._api_value = dict(payload)
    return entity


def amocrm_get(obj_cls: Type[BaseEntity],
               page: int = None,
               limit: int = 250,
               _filter: dict = None,
               _with: str = None,
               query: Any = None,
               custom_filter: dict = None) -> Iterator[BaseEntity]:
    params = {}
    if page:
        params['page'] = page
    if limit:
        params['limit'] = limit
    if _filter:
        for key, value in _filter.items():
            filter_key = f'filter[{key}]'
            if isinstance(value, list):
                filter_key = f'{filter_key}[]'
            params[filter_key] = value
    if _with:
        if isinstance(_with, list):
            params['with'] = ",".join(_with)
        else:
            params['with'] = _with
    if query:
        params[f'query'] = query

    headers = {}
    base_url = ""
    url = f"{base_url}{obj_cls.url}"
    entity_name = obj_cls.entity_name
    while True:
        json_data = None
        for try_attempt in range(4):
            response = None
            try:
                response = requests.get(url, params=params, headers=headers, timeout=120)
                json_data = response_handler(response)
                break
            except AmoCrmRepeatError:
                if try_attempt >= 3:
                    raise
            except (AmoCrmNoConten, AmoCrmError):
                raise
            except requests.RequestException as e:
                raise AmoCrmError(
                    f"[!] {response.status_code if response else '<No code!>'} | "
                    f"Unexpected AmoCRM request error: {e}"
                ) from e
        if not isinstance(json_data, dict):
            raise AmoCrmError("AmoCRM response must be a JSON object")
        embedded = json_data.get("_embedded")
        if not isinstance(embedded, dict) or entity_name not in embedded:
            raise AmoCrmError(
                f"AmoCRM response is missing _embedded.{entity_name}"
            )
        entities = embedded[entity_name]
        if not isinstance(entities, list):
            raise AmoCrmError(
                f"AmoCRM response _embedded.{entity_name} must be a list"
            )

        if json_data is not None:
            for payload in entities:
                entity = _entity_from_api_payload(obj_cls, payload)
                skip_entity = False
                if custom_filter:
                    # if custom filter is not None then check custom field,
                    # if at least one not equal filter, set skip_entity value True and break checker
                    #
                    for key, value in custom_filter.items():
                        if entity.get_field(key) != value:
                            skip_entity = True
                            break
                # check if should pass entity
                if skip_entity:
                    pass
                else:
                    yield entity

            # condition to stop while loop
            if page:
                break
            next_url = (
                json_data.get("_links", {})
                .get("next", {})
                .get("href")
            )
            if not next_url:
                break
            time.sleep(1)
            url = next_url
            params = {}


def response_handler(response):
    if response.status_code == 204:
        raise AmoCrmNoConten(f"no content for {response.url}, method: {response.request.method}")
    elif response.status_code == 429:
        time.sleep(2)
        raise AmoCrmRepeatError()
    elif response.status_code != 200:
        json_data = {}
        try:
            json_data = response.json()
        except ValueError:
            pass
        raise AmoCrmError(error_code=response.status_code, msg=json_data)
    try:
        return response.json()
    except ValueError as e:
        raise AmoCrmError(
            "AmoCRM returned invalid JSON",
            error_code=response.status_code,
        ) from e
