import logging
from collections import defaultdict
from typing import Any, Dict, Iterable, Iterator, Optional, Set, Tuple

from sqlalchemy import select

from abstract_customer import dict_compare_merge, get_customer_json, get_lead_json
from amocrm import COMPANIES, CONTACTS, Customer, Lead, amocrm_get
from models import AbstractCustomer, db

logger = logging.getLogger(__name__)

ENTITY_FIELDS = (
    "amo_customers",
    "amo_leads",
    "amo_contacts",
    "amo_companies",
)
LOGIN_FIELDS = {"amo_customers", "amo_leads"}


class DuplicateCrmEntityIdError(RuntimeError):
    """Raised when one CRM entity ID belongs to multiple customer records."""

    def __init__(self, entity_type: str, crm_id: Any, record_count: int):
        message = (
            f"CRM {entity_type} ID {crm_id!r} is present in "
            f"{record_count} AbstractCustomer records"
        )
        super().__init__(message)


def _entity_id(entity: Dict[str, Any]) -> Any:
    return entity.get("general", {}).get("id")


def _entity_login(entity: Dict[str, Any]) -> Optional[str]:
    login = entity.get("general", {}).get("login")
    return login if isinstance(login, str) and login else None


def _index_row(
        row: AbstractCustomer,
        row_key: int,
        id_index: Dict[str, Dict[Any, Set[int]]],
        login_index: Dict[str, Dict[str, Set[int]]],
        login_counts: Dict[Tuple[int, str, str], int],
) -> None:
    for field in ENTITY_FIELDS:
        for entity in getattr(row, field) or []:
            entity_id = _entity_id(entity)
            if entity_id is not None:
                id_index[field][entity_id].add(row_key)
            login = _entity_login(entity) if field in LOGIN_FIELDS else None
            if login:
                count_key = (row_key, field, login)
                login_counts[count_key] = login_counts.get(count_key, 0) + 1
                login_index[field][login].add(row_key)


def _unindex_row(
        row: AbstractCustomer,
        row_key: int,
        id_index: Dict[str, Dict[Any, Set[int]]],
        login_index: Dict[str, Dict[str, Set[int]]],
        login_counts: Dict[Tuple[int, str, str], int],
) -> None:
    for field in ENTITY_FIELDS:
        logins = set()
        for entity in getattr(row, field) or []:
            entity_id = _entity_id(entity)
            if entity_id is not None:
                rows = id_index[field].get(entity_id)
                if rows:
                    rows.discard(row_key)
                    if not rows:
                        del id_index[field][entity_id]
            login = _entity_login(entity) if field in LOGIN_FIELDS else None
            if login:
                logins.add(login)
        for login in logins:
            login_counts.pop((row_key, field, login), None)
            rows = login_index[field].get(login)
            if rows:
                rows.discard(row_key)
                if not rows:
                    del login_index[field][login]


def _add_login(
        row_key: int,
        field: str,
        login: Optional[str],
        login_index: Dict[str, Dict[str, Set[int]]],
        login_counts: Dict[Tuple[int, str, str], int],
) -> None:
    if not login:
        return
    count_key = (row_key, field, login)
    if login_counts.get(count_key, 0) == 0:
        login_index[field][login].add(row_key)
    login_counts[count_key] = login_counts.get(count_key, 0) + 1


def _remove_login(
        row_key: int,
        field: str,
        login: Optional[str],
        login_index: Dict[str, Dict[str, Set[int]]],
        login_counts: Dict[Tuple[int, str, str], int],
) -> None:
    if not login:
        return
    count_key = (row_key, field, login)
    count = login_counts.get(count_key, 0) - 1
    if count <= 0:
        login_counts.pop(count_key, None)
        rows = login_index[field].get(login)
        if rows:
            rows.discard(row_key)
            if not rows:
                del login_index[field][login]
    else:
        login_counts[count_key] = count


def _upsert_entity(
        row: AbstractCustomer,
        row_key: int,
        field: str,
        entity: Dict[str, Any],
        id_index: Dict[str, Dict[Any, Set[int]]],
        login_index: Dict[str, Dict[str, Set[int]]],
        login_counts: Dict[Tuple[int, str, str], int],
) -> None:
    entities = list(getattr(row, field) or [])
    entity_id = _entity_id(entity)
    existing_index = next(
        (index for index, value in enumerate(entities)
         if _entity_id(value) == entity_id),
        None,
    )
    new_login = _entity_login(entity) if field in LOGIN_FIELDS else None

    if existing_index is None:
        entities.append(entity)
        if entity_id is not None:
            id_index[field][entity_id].add(row_key)
        _add_login(row_key, field, new_login, login_index, login_counts)
    else:
        old_login = (
            _entity_login(entities[existing_index])
            if field in LOGIN_FIELDS else None
        )
        if old_login != new_login:
            _remove_login(row_key, field, old_login, login_index, login_counts)
            _add_login(row_key, field, new_login, login_index, login_counts)
        entities[existing_index] = entity

    setattr(row, field, entities)


def _single_login_match(
        login: str,
        field: str,
        login_index: Dict[str, Dict[str, Set[int]]],
        rows: Dict[int, AbstractCustomer],
        entity_name: str,
) -> Optional[AbstractCustomer]:
    matches = login_index[field].get(login, set())
    if len(matches) > 1:
        logger.warning("%s %s has more than one abstract customer", entity_name, login)
        return None
    if not matches:
        return None
    return rows[next(iter(matches))]


def _customer_by_id(
        customer_id: Any,
        id_index: Dict[str, Dict[Any, Set[int]]],
        rows: Dict[int, AbstractCustomer],
) -> Optional[AbstractCustomer]:
    matches = id_index["amo_customers"].get(customer_id, set())
    if len(matches) > 1:
        logger.error(
            "Duplicate CRM customer ID %r found in %d AbstractCustomer records",
            customer_id,
            len(matches),
        )
        raise DuplicateCrmEntityIdError("customer", customer_id, len(matches))
    return rows[next(iter(matches))] if matches else None


def _lead_by_id(
        lead_id: Any,
        id_index: Dict[str, Dict[Any, Set[int]]],
        rows: Dict[int, AbstractCustomer],
) -> Optional[AbstractCustomer]:
    matches = id_index["amo_leads"].get(lead_id, set())
    if len(matches) > 1:
        logger.error(
            "Duplicate CRM lead ID %r found in %d AbstractCustomer records",
            lead_id,
            len(matches),
        )
        raise DuplicateCrmEntityIdError("lead", lead_id, len(matches))
    return rows[next(iter(matches))] if matches else None


def _new_row(session: Any, rows: Dict[int, AbstractCustomer]) -> AbstractCustomer:
    row = AbstractCustomer()
    session.add(row)
    rows[id(row)] = row
    return row


def _mark_seen(
        seen: Dict[int, Dict[str, Set[Any]]],
        row_key: int,
        field: str,
        entity_ids: Iterable[Any],
) -> None:
    seen.setdefault(row_key, defaultdict(set))[field].update(
        entity_id for entity_id in entity_ids if entity_id is not None
    )


def _merge_rows(
        target: AbstractCustomer,
        source: AbstractCustomer,
        session: Any,
        rows: Dict[int, AbstractCustomer],
        seen: Dict[int, Dict[str, Set[Any]]],
        id_index: Dict[str, Dict[Any, Set[int]]],
        login_index: Dict[str, Dict[str, Set[int]]],
        login_counts: Dict[Tuple[int, str, str], int],
) -> None:
    target_key = id(target)
    source_key = id(source)
    _unindex_row(target, target_key, id_index, login_index, login_counts)
    _unindex_row(source, source_key, id_index, login_index, login_counts)

    for field in ENTITY_FIELDS:
        setattr(
            target,
            field,
            dict_compare_merge(
                getattr(target, field) or [],
                getattr(source, field) or [],
            ),
        )

    send_methods = []
    send_method_keys = set()
    for send_method in (target.send_methods or []) + (source.send_methods or []):
        key = (send_method.get("type"), send_method.get("value"))
        if key not in send_method_keys:
            send_method_keys.add(key)
            send_methods.append(send_method.copy())
    setattr(target, "send_methods", send_methods)

    for field, entity_ids in seen.pop(source_key, {}).items():
        seen.setdefault(target_key, defaultdict(set))[field].update(entity_ids)
    rows.pop(source_key, None)
    _index_row(target, target_key, id_index, login_index, login_counts)
    session.delete(source)


def _clean_full_snapshot(
        rows: Dict[int, AbstractCustomer],
        seen: Dict[int, Dict[str, Set[Any]]],
        session: Any,
) -> None:
    for row_key, row in list(rows.items()):
        observed = seen.get(row_key)
        if observed is None:
            session.delete(row)
            continue

        for field in ENTITY_FIELDS:
            entity_ids = observed.get(field, set())
            current = getattr(row, field) or []
            kept = [
                entity for entity in current
                if _entity_id(entity) in entity_ids
                and "удалить" not in (
                    entity.get("general", {}).get("name") or ""
                ).lower()
            ]
            if len(kept) != len(current):
                setattr(row, field, kept)

        if not row.amo_customers and not row.amo_leads:
            session.delete(row)


def collect_leads_customers(is_full: bool = True) -> None:
    """Synchronize CRM customers and leads in one database transaction."""
    session = db.session
    try:
        # Load the existing JSON once; the indexes below replace per-entity JSONB queries.
        rows = {
            id(row): row
            for row in session.scalars(select(AbstractCustomer)).all()
        }
        id_index = defaultdict(lambda: defaultdict(set))
        login_index = defaultdict(lambda: defaultdict(set))
        login_counts: Dict[Tuple[int, str, str], int] = {}
        for row_key, row in rows.items():
            _index_row(row, row_key, id_index, login_index, login_counts)

        seen: Dict[int, Dict[str, Set[Any]]] = {}
        customers: Iterator[Customer] = amocrm_get(
            Customer, _with=f"{COMPANIES},{CONTACTS}"
        )
        logger.info("Collected customers is_full=%s", is_full)
        for customer in customers:
            if not is_full and customer.status_id == 111349:
                continue
            customer_login = customer.get_field("Логин")
            if not customer_login:
                continue

            row = _customer_by_id(customer.id, id_index, rows)
            if row is None:
                row = _single_login_match(
                    customer_login, "amo_leads", login_index, rows, "Lead"
                )
            if row is None:
                row = _new_row(session, rows)

            row_key = id(row)
            _upsert_entity(
                row, row_key, "amo_customers", get_customer_json(customer),
                id_index, login_index, login_counts,
            )
            if is_full:
                _mark_seen(seen, row_key, "amo_customers", [customer.id])
                _mark_seen(
                    seen, row_key, "amo_contacts",
                    [contact.get("id") for contact in customer.contacts],
                )
                _mark_seen(
                    seen, row_key, "amo_companies",
                    [company.get("id") for company in customer.companies],
                )

        del customers
        leads: Iterator[Lead] = amocrm_get(Lead, _with=f"{CONTACTS}")
        logger.info("Collected leads is_full=%s", is_full)
        for lead in leads:
            if not is_full and lead.status_id == 143:
                continue

            row = _lead_by_id(lead.id, id_index, rows)
            login = lead.get_field("Логин")
            customer_row = None
            if login:
                customer_row = _single_login_match(
                    login, "amo_customers", login_index, rows, "Customer"
                )
            if row is None:
                row = customer_row
            elif customer_row is not None and customer_row is not row:
                _merge_rows(
                    row, customer_row, session, rows, seen,
                    id_index, login_index, login_counts,
                )
            if row is None:
                row = _new_row(session, rows)

            row_key = id(row)
            _upsert_entity(
                row, row_key, "amo_leads", get_lead_json(lead),
                id_index, login_index, login_counts,
            )
            if is_full:
                _mark_seen(seen, row_key, "amo_leads", [lead.id])
                _mark_seen(
                    seen, row_key, "amo_contacts",
                    [contact.get("id") for contact in lead.contacts],
                )

        if is_full:
            _clean_full_snapshot(rows, seen, session)
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("CRM customer/lead synchronization failed")
        raise
