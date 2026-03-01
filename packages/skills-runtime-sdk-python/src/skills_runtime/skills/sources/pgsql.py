from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Mapping, MutableMapping

from skills_runtime.config.loader import AgentSdkSkillsConfig
from skills_runtime.core.errors import FrameworkError, FrameworkIssue
from skills_runtime.skills.mentions import is_valid_skill_name_slug
from skills_runtime.skills.models import Skill
from skills_runtime.skills.sources._utils import ensure_metadata_string, normalize_optional_int, safe_identifier


def get_pgsql_client(
    *,
    source: AgentSdkSkillsConfig.Source,
    source_clients: Mapping[str, Any],
    source_dsn_from_env,
) -> Any:
    """
    Get pgsql client (prefer injected; else initialize from dsn_env).

    Note:
    - We intentionally avoid caching a single connection by default.
    """

    injected = source_clients.get(source.id)
    if injected is not None:
        return injected

    dsn = source_dsn_from_env(source)
    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError as exc:
        dsn_env = source.options.get("dsn_env")
        raise FrameworkError(
            code="SKILL_SCAN_SOURCE_UNAVAILABLE",
            message="Skill source is unavailable in current runtime.",
            details={
                "source_id": source.id,
                "source_type": source.type,
                "dsn_env": dsn_env,
                "env_present": True,
                "reason": f"psycopg dependency unavailable: {exc}",
            },
        ) from exc

    try:
        client = psycopg.connect(dsn)
    except Exception as exc:
        dsn_env = source.options.get("dsn_env")
        raise FrameworkError(
            code="SKILL_SCAN_SOURCE_UNAVAILABLE",
            message="Skill source is unavailable in current runtime.",
            details={
                "source_id": source.id,
                "source_type": source.type,
                "dsn_env": dsn_env,
                "env_present": True,
                "reason": f"pgsql connect failed: {exc}",
            },
        ) from exc
    return client


@contextlib.contextmanager
def pgsql_client_context(
    *,
    source: AgentSdkSkillsConfig.Source,
    source_clients: MutableMapping[str, Any],
    get_pgsql_client_for_source,
) -> Iterator[Any]:
    """
    Context manager for getting a pgsql client (supports injected factory/pool).

    Behavior:
    - default: allocate connection per use and close on exit (if possible).
    - injected pool: has .connection() context manager; enter/exit handled here.
    - injected factory: callable returning a client; closed here if possible.
    - injected direct client: yielded as-is; not closed here.
    """

    injected = source_clients.get(source.id)
    if injected is not None:
        conn_cm = getattr(injected, "connection", None)
        if callable(conn_cm):
            with conn_cm() as client:
                yield client
            return

        if callable(injected):
            client = injected()
            try:
                if hasattr(client, "__enter__") and hasattr(client, "__exit__"):
                    with client as inner:
                        yield inner
                else:
                    yield client
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()
            return

        yield injected
        return

    client = get_pgsql_client_for_source(source)
    try:
        yield client
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()


def fetchall_as_rows(cursor: Any) -> List[Dict[str, Any]]:
    """Normalize cursor.fetchall() results to a list of dict rows."""

    rows = cursor.fetchall()
    if not rows:
        return []

    if isinstance(rows[0], Mapping):
        return [dict(row) for row in rows]

    description = getattr(cursor, "description", None)
    if not description:
        raise TypeError("cursor.description is required for tuple rows")

    columns = [col[0] for col in description]
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)):
            raise TypeError(f"unsupported row type: {type(row)!r}")
        out.append(dict(zip(columns, row, strict=False)))
    return out


def scan_pgsql_source(
    *,
    space: AgentSdkSkillsConfig.Space,
    source: AgentSdkSkillsConfig.Source,
    sink: List[Skill],
    errors: List[FrameworkIssue],
    pgsql_client_context_for_source,
) -> None:
    """Scan pgsql source (metadata-only)."""

    try:
        schema = safe_identifier(source.options.get("schema"), field="schema", source_id=source.id)
        table = safe_identifier(source.options.get("table"), field="table", source_id=source.id)
    except FrameworkError as exc:
        errors.append(exc.to_issue())
        return

    try:
        table_ref = f'"{schema}"."{table}"'
    except Exception:
        table_ref = f'"{schema}"."{table}"'
    sql = (
        "SELECT id, namespace, skill_name, description, body_size, body_etag, created_at, updated_at, "
        "required_env_vars, metadata, scope "
        f"FROM {table_ref} "
        "WHERE enabled = TRUE AND namespace = %s"
    )

    try:
        with pgsql_client_context_for_source(source) as client:
            with client.cursor() as cursor:
                cursor.execute(sql, (space.namespace,))
                rows = fetchall_as_rows(cursor)
    except FrameworkError as exc:
        errors.append(exc.to_issue())
        return
    except Exception as exc:
        errors.append(
            FrameworkIssue(
                code="SKILL_SCAN_SOURCE_UNAVAILABLE",
                message="Skill source is unavailable in current runtime.",
                details={
                    "source_id": source.id,
                    "source_type": source.type,
                    "reason": f"pgsql query failed: {exc}",
                },
            )
        )
        return

    for row in rows:
        locator = f"{schema}.{table}#{row.get('id')}"
        try:
            skill_name = ensure_metadata_string(row.get("skill_name"), field="skill_name", source_id=source.id, locator=locator)
            if not is_valid_skill_name_slug(skill_name):
                raise FrameworkError(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "locator": locator, "field": "skill_name", "actual": skill_name},
                )
            description = ensure_metadata_string(row.get("description"), field="description", source_id=source.id, locator=locator)
            body_size = normalize_optional_int(row.get("body_size"), field="body_size", source_id=source.id, locator=locator)

            created_at_raw = row.get("created_at")
            if created_at_raw is None:
                raise FrameworkError(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "locator": locator, "field": "created_at"},
                )
            if isinstance(created_at_raw, datetime):
                created_at = created_at_raw.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            elif isinstance(created_at_raw, str) and created_at_raw:
                created_at = created_at_raw
            else:
                raise FrameworkError(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "locator": locator, "field": "created_at"},
                )

            required_env_vars_raw = row.get("required_env_vars")
            required_env_vars: List[str]
            if required_env_vars_raw is None:
                required_env_vars = []
            elif isinstance(required_env_vars_raw, list) and all(isinstance(v, str) for v in required_env_vars_raw):
                required_env_vars = list(required_env_vars_raw)
            else:
                raise FrameworkError(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "locator": locator, "field": "required_env_vars"},
                )

            metadata_raw = row.get("metadata")
            metadata_obj: Dict[str, Any]
            if metadata_raw is None:
                metadata_obj = {}
            elif isinstance(metadata_raw, dict):
                metadata_obj = dict(metadata_raw)
            else:
                raise FrameworkError(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "locator": locator, "field": "metadata"},
                )

            row_id = row.get("id")
            if row_id is None:
                raise FrameworkError(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "locator": locator, "field": "id"},
                )

            scope = row.get("scope")
            if scope is not None and not isinstance(scope, str):
                raise FrameworkError(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "locator": locator, "field": "scope"},
                )

            body_etag = row.get("body_etag")
            if body_etag is not None and not isinstance(body_etag, str):
                raise FrameworkError(
                    code="SKILL_SCAN_METADATA_INVALID",
                    message="Skill metadata is invalid.",
                    details={"source_id": source.id, "locator": locator, "field": "body_etag"},
                )

            updated_at = row.get("updated_at")
            if updated_at is not None and not isinstance(updated_at, str):
                updated_at = str(updated_at)

            def _load_body(
                pgsql_client_context_ref=pgsql_client_context_for_source,
                source_ref: AgentSdkSkillsConfig.Source = source,
                schema_ref: str = schema,
                table_ref_inner: str = table,
                row_id_ref: Any = row_id,
                namespace_ref: str = space.namespace,
            ) -> str:
                """延迟加载 skill body（按 row_id + namespace 回表查询）。"""
                sql_body = f'SELECT body FROM "{schema_ref}"."{table_ref_inner}" ' "WHERE id = %s AND namespace = %s"
                with pgsql_client_context_ref(source_ref) as client:
                    with client.cursor() as body_cursor:
                        body_cursor.execute(sql_body, (row_id_ref, namespace_ref))
                        rec = body_cursor.fetchone()
                if rec is None:
                    raise FileNotFoundError(f"missing body row: {schema_ref}.{table_ref_inner}#{row_id_ref}")
                if isinstance(rec, Mapping):
                    body_val = rec.get("body")
                elif isinstance(rec, (tuple, list)):
                    body_val = rec[0] if rec else None
                else:
                    body_val = rec
                if not isinstance(body_val, str):
                    raise TypeError(f"invalid body type: {type(body_val)!r}")
                return body_val

            sink.append(
                Skill(
                    space_id=space.id,
                    source_id=source.id,
                    namespace=space.namespace,
                    skill_name=skill_name,
                    description=description,
                    locator=locator,
                    path=None,
                    body_size=body_size,
                    body_loader=_load_body,
                    required_env_vars=required_env_vars,
                    metadata={**metadata_obj, "etag": body_etag, "created_at": created_at, "updated_at": updated_at, "row_id": row_id},
                    scope=scope,
                )
            )
        except FrameworkError as exc:
            errors.append(exc.to_issue())
