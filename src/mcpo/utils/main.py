import json
import traceback
from typing import Any, Dict, ForwardRef, List, Optional, Type, Union
import logging
from fastapi import HTTPException, Request
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

from mcp import ClientSession, types
from mcp.types import (
    CallToolResult,
    PARSE_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    INVALID_PARAMS,
    INTERNAL_ERROR,
)

from mcp.shared.exceptions import McpError

from pydantic import Field, create_model
from pydantic.fields import FieldInfo

MCP_ERROR_TO_HTTP_STATUS = {
    PARSE_ERROR: 400,
    INVALID_REQUEST: 400,
    METHOD_NOT_FOUND: 404,
    INVALID_PARAMS: 422,
    INTERNAL_ERROR: 500,
}

logger = logging.getLogger(__name__)


def process_tool_response(result: CallToolResult) -> list:
    """Universal response processor for all tool endpoints"""
    response = []
    for content in result.content:
        if isinstance(content, types.TextContent):
            text = content.text
            if isinstance(text, str):
                try:
                    text = json.loads(text)
                except json.JSONDecodeError:
                    pass
            response.append(text)
        elif isinstance(content, types.ImageContent):
            image_data = f"data:{content.mimeType};base64,{content.data}"
            response.append(image_data)
        elif isinstance(content, types.EmbeddedResource):
            # TODO: Handle embedded resources
            response.append("Embedded resource not supported yet.")
    return response


def name_needs_alias(name: str) -> bool:
    """Check if a field name needs aliasing (for now if it starts with '__')."""
    return name.startswith("__")


def generate_alias_name(original_name: str, existing_names: set) -> str:
    """
    Generate an alias field name by stripping unwanted chars, and avoiding conflicts with existing names.

    Args:
        original_name: The original field name (should start with '__')
        existing_names: Set of existing names to avoid conflicts with

    Returns:
        An alias name that doesn't conflict with existing names
    """
    alias_name = original_name.lstrip("_")
    # Handle potential naming conflicts
    original_alias_name = alias_name
    suffix_counter = 1
    while alias_name in existing_names:
        alias_name = f"{original_alias_name}_{suffix_counter}"
        suffix_counter += 1
    return alias_name


async def call_tool_with_forwarded_auth(
    request: Request,
    app,
    session: ClientSession,
    endpoint_name: str,
    arguments: dict,
) -> CallToolResult:
    """Call a tool, forwarding the Authorization header if provided."""

    auth_header = request.headers.get("Authorization")
    server_type = getattr(app.state, "server_type", "stdio")

    if auth_header and server_type in {"sse", "streamablehttp", "streamable_http"}:
        base_headers = getattr(app.state, "headers", {}) or {}
        if isinstance(base_headers, str):
            try:
                base_headers = json.loads(base_headers)
            except Exception:
                base_headers = {}

        headers = dict(base_headers)
        headers["Authorization"] = auth_header

        if server_type == "sse":
            async with sse_client(url=app.state.args, headers=headers) as (reader, writer):
                async with ClientSession(reader, writer) as temp_session:
                    return await temp_session.call_tool(endpoint_name, arguments=arguments)
        else:
            url = app.state.args
            if not url.endswith("/"):
                url = f"{url}/"
            async with streamablehttp_client(url=url, headers=headers) as (
                reader,
                writer,
                _,
            ):
                async with ClientSession(reader, writer) as temp_session:
                    return await temp_session.call_tool(endpoint_name, arguments=arguments)

    return await session.call_tool(endpoint_name, arguments=arguments)


def _process_schema_property(
    _model_cache: Dict[str, Type],
    prop_schema: Dict[str, Any],
    model_name_prefix: str,
    prop_name: str,
    is_required: bool,
    schema_defs: Optional[Dict] = None,
) -> tuple[Union[Type, List, ForwardRef, Any], FieldInfo]:
    """
    Recursively processes a schema property to determine its Python type hint
    and Pydantic Field definition.

    Returns:
        A tuple containing (python_type_hint, pydantic_field).
        The pydantic_field contains default value and description.
    """
    if "$ref" in prop_schema:
        ref = prop_schema["$ref"]
        if ref.startswith("#/properties/"):
            # Remove common prefix in pathes.
            prefix_path = model_name_prefix.split("_form_model_")[-1]
            ref_path = ref.split("#/properties/")[-1]
            # Translate $ref path to model_name_prefix style.
            ref_path = ref_path.replace("/properties/", "_model_")
            ref_path = ref_path.replace("/items", "_item")
            # If $ref path is a prefix substring of model_name_prefix path,
            # there exists a circular reference.
            # The loop should be broke with a return to avoid exception.
            if prefix_path.startswith(ref_path):
                # TODO: Find the exact type hint for the $ref.
                return Any, Field(default=None, description="")
        ref = ref.split("/")[-1]
        assert ref in schema_defs, "Custom field not found"
        prop_schema = schema_defs[ref]

    prop_type = prop_schema.get("type")
    prop_desc = prop_schema.get("description", "")

    default_value = ... if is_required else prop_schema.get("default", None)
    pydantic_field = Field(default=default_value, description=prop_desc)

    # Handle the case where prop_type is missing but 'anyOf' key exists
    # In this case, use data type from 'anyOf' to determine the type hint
    if "anyOf" in prop_schema:
        type_hints = []
        for i, schema_option in enumerate(prop_schema["anyOf"]):
            type_hint, _ = _process_schema_property(
                _model_cache,
                schema_option,
                f"{model_name_prefix}_{prop_name}",
                f"choice_{i}",
                False,
            )
            type_hints.append(type_hint)
        return Union[tuple(type_hints)], pydantic_field

    # Handle the case where prop_type is a list of types, e.g. ['string', 'number']
    if isinstance(prop_type, list):
        # Create a Union of all the types
        type_hints = []
        for type_option in prop_type:
            # Create a temporary schema with the single type and process it
            temp_schema = dict(prop_schema)
            temp_schema["type"] = type_option
            type_hint, _ = _process_schema_property(
                _model_cache, temp_schema, model_name_prefix, prop_name, False
            )
            type_hints.append(type_hint)

        # Return a Union of all possible types
        return Union[tuple(type_hints)], pydantic_field

    if prop_type == "object":
        nested_properties = prop_schema.get("properties", {})
        nested_required = prop_schema.get("required", [])
        nested_fields = {}

        nested_model_name = f"{model_name_prefix}_{prop_name}_model".replace(
            "__", "_"
        ).rstrip("_")

        if nested_model_name in _model_cache:
            return _model_cache[nested_model_name], pydantic_field

        for name, schema in nested_properties.items():
            is_nested_required = name in nested_required
            nested_type_hint, nested_pydantic_field = _process_schema_property(
                _model_cache,
                schema,
                nested_model_name,
                name,
                is_nested_required,
                schema_defs,
            )

            if name_needs_alias(name):
                other_names = set().union(
                    nested_properties, nested_fields, _model_cache
                )
                alias_name = generate_alias_name(name, other_names)
                aliased_field = Field(
                    default=nested_pydantic_field.default,
                    description=nested_pydantic_field.description,
                    alias=name,
                )
                nested_fields[alias_name] = (nested_type_hint, aliased_field)
            else:
                nested_fields[name] = (nested_type_hint, nested_pydantic_field)

        if not nested_fields:
            return Dict[str, Any], pydantic_field

        NestedModel = create_model(nested_model_name, **nested_fields)
        _model_cache[nested_model_name] = NestedModel

        return NestedModel, pydantic_field

    elif prop_type == "array":
        items_schema = prop_schema.get("items")
        if not items_schema:
            # Default to list of anything if items schema is missing
            return List[Any], pydantic_field

        # Recursively determine the type of items in the array
        item_type_hint, _ = _process_schema_property(
            _model_cache,
            items_schema,
            f"{model_name_prefix}_{prop_name}",
            "item",
            False,  # Items aren't required at this level,
            schema_defs,
        )
        list_type_hint = List[item_type_hint]
        return list_type_hint, pydantic_field

    elif prop_type == "string":
        return str, pydantic_field
    elif prop_type == "integer":
        return int, pydantic_field
    elif prop_type == "boolean":
        return bool, pydantic_field
    elif prop_type == "number":
        return float, pydantic_field
    elif prop_type == "null":
        return None, pydantic_field
    else:
        return Any, pydantic_field


def get_model_fields(form_model_name, properties, required_fields, schema_defs=None):
    model_fields = {}

    _model_cache: Dict[str, Type] = {}

    for param_name, param_schema in properties.items():
        is_required = param_name in required_fields
        python_type_hint, pydantic_field_info = _process_schema_property(
            _model_cache,
            param_schema,
            form_model_name,
            param_name,
            is_required,
            schema_defs,
        )

        # Handle parameter names with leading underscores (e.g., __top, __filter) which Pydantic v2 does not allow
        if name_needs_alias(param_name):
            other_names = set().union(properties, model_fields, _model_cache)
            alias_name = generate_alias_name(param_name, other_names)
            aliased_field = Field(
                default=pydantic_field_info.default,
                description=pydantic_field_info.description,
                alias=param_name,
            )
            # Use the generated type hint and Field info
            model_fields[alias_name] = (python_type_hint, aliased_field)
        else:
            model_fields[param_name] = (python_type_hint, pydantic_field_info)

    return model_fields


def get_tool_handler(
    app,
    session,
    endpoint_name,
    form_model_fields,
    response_model_fields=None,
):
    if form_model_fields:
        FormModel = create_model(f"{endpoint_name}_form_model", **form_model_fields)
        ResponseModel = (
            create_model(f"{endpoint_name}_response_model", **response_model_fields)
            if response_model_fields
            else Any
        )

        def make_endpoint_func(
            endpoint_name: str, FormModel, session: ClientSession
        ):  # Parameterized endpoint
            async def tool(request: Request, form_data: FormModel) -> Union[ResponseModel, Any]:
                args = form_data.model_dump(exclude_none=True, by_alias=True)
                logger.info(f"Calling endpoint: {endpoint_name}, with args: {args}")
                try:
                    result = await call_tool_with_forwarded_auth(
                        request,
                        app,
                        session,
                        endpoint_name,
                        args,
                    )

                    if result.isError:
                        error_message = "Unknown tool execution error"
                        error_data = None  # Initialize error_data
                        if result.content:
                            if isinstance(result.content[0], types.TextContent):
                                error_message = result.content[0].text
                        detail = {"message": error_message}
                        if error_data is not None:
                            detail["data"] = error_data
                        raise HTTPException(
                            status_code=500,
                            detail=detail,
                        )

                    response_data = process_tool_response(result)
                    final_response = (
                        response_data[0] if len(response_data) == 1 else response_data
                    )
                    return final_response

                except McpError as e:
                    logger.info(
                        f"MCP Error calling {endpoint_name}: {traceback.format_exc()}"
                    )
                    status_code = MCP_ERROR_TO_HTTP_STATUS.get(e.error.code, 500)
                    raise HTTPException(
                        status_code=status_code,
                        detail=(
                            {"message": e.error.message, "data": e.error.data}
                            if e.error.data is not None
                            else {"message": e.error.message}
                        ),
                    )
                except Exception as e:
                    logger.info(
                        f"Unexpected error calling {endpoint_name}: {traceback.format_exc()}"
                    )
                    raise HTTPException(
                        status_code=500,
                        detail={"message": "Unexpected error", "error": str(e)},
                    )

            return tool

        tool_handler = make_endpoint_func(endpoint_name, FormModel, session)
    else:

        def make_endpoint_func_no_args(
            endpoint_name: str, session: ClientSession
        ):  # Parameterless endpoint
            async def tool(request: Request):  # No parameters
                logger.info(f"Calling endpoint: {endpoint_name}, with no args")
                try:
                    result = await call_tool_with_forwarded_auth(
                        request,
                        app,
                        session,
                        endpoint_name,
                        {},
                    )

                    if result.isError:
                        error_message = "Unknown tool execution error"
                        if result.content:
                            if isinstance(result.content[0], types.TextContent):
                                error_message = result.content[0].text
                        detail = {"message": error_message}
                        raise HTTPException(
                            status_code=500,
                            detail=detail,
                        )

                    response_data = process_tool_response(result)
                    final_response = (
                        response_data[0] if len(response_data) == 1 else response_data
                    )
                    return final_response

                except McpError as e:
                    logger.info(
                        f"MCP Error calling {endpoint_name}: {traceback.format_exc()}"
                    )
                    status_code = MCP_ERROR_TO_HTTP_STATUS.get(e.error.code, 500)
                    # Propagate the error received from MCP as an HTTP exception
                    raise HTTPException(
                        status_code=status_code,
                        detail=(
                            {"message": e.error.message, "data": e.error.data}
                            if e.error.data is not None
                            else {"message": e.error.message}
                        ),
                    )
                except Exception as e:
                    logger.info(
                        f"Unexpected error calling {endpoint_name}: {traceback.format_exc()}"
                    )
                    raise HTTPException(
                        status_code=500,
                        detail={"message": "Unexpected error", "error": str(e)},
                    )

            return tool

        tool_handler = make_endpoint_func_no_args(endpoint_name, session)

    return tool_handler
