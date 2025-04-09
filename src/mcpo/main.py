import json
import os
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Dict, Any, Optional, List, Type, Union, ForwardRef

import uvicorn
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult

from mcpo.utils.auth import get_verify_api_key
from pydantic import create_model, Field
from pydantic.fields import FieldInfo
from starlette.routing import Mount


_model_cache: Dict[str, Type] = {}

def _process_schema_property(
    prop_schema: Dict[str, Any],
    model_name_prefix: str,
    prop_name: str,
    is_required: bool,
) -> tuple[Union[Type, List, ForwardRef, Any], FieldInfo]:
    """
    Recursively processes a schema property to determine its Python type hint
    and Pydantic Field definition.

    Returns:
        A tuple containing (python_type_hint, pydantic_field).
        The pydantic_field contains default value and description.
    """
    prop_type = prop_schema.get("type")
    prop_desc = prop_schema.get("description", "")
    default_value = ... if is_required else prop_schema.get("default", None)
    pydantic_field = Field(default=default_value, description=prop_desc)

    if prop_type == "object":
        nested_properties = prop_schema.get("properties", {})
        nested_required = prop_schema.get("required", [])
        nested_fields = {}

        nested_model_name = f"{model_name_prefix}_{prop_name}_model".replace("__", "_").rstrip('_')

        if nested_model_name in _model_cache:
            return _model_cache[nested_model_name], pydantic_field

        for name, schema in nested_properties.items():
            is_nested_required = name in nested_required
            nested_type_hint, nested_pydantic_field = _process_schema_property(
                schema, nested_model_name, name, is_nested_required
            )

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
            items_schema, f"{model_name_prefix}_{prop_name}", "item", False # Items aren't required at this level
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
    else:
        return Any, pydantic_field


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


async def create_dynamic_endpoints(app: FastAPI, api_dependency=None):
    session = app.state.session
    if not session:
        raise ValueError("Session is not initialized in the app state.")

    result = await session.initialize()
    server_info = getattr(result, "serverInfo", None)
    if server_info:
        app.title = server_info.name or app.title
        app.description = (
            f"{server_info.name} MCP Server" if server_info.name else app.description
        )
        app.version = server_info.version or app.version

    tools_result = await session.list_tools()
    tools = tools_result.tools

    for tool in tools:
        endpoint_name = tool.name
        endpoint_description = tool.description
        schema = tool.inputSchema

        model_fields = {}
        required_fields = schema.get("required", [])
        properties = schema.get("properties", {})

        form_model_name = f"{endpoint_name}_form_model"
        for param_name, param_schema in properties.items():
            is_required = param_name in required_fields
            python_type_hint, pydantic_field_info = _process_schema_property(
                param_schema, form_model_name, param_name, is_required
            )
            # Use the generated type hint and Field info
            model_fields[param_name] = (python_type_hint, pydantic_field_info)

        if model_fields:
            FormModel = create_model(form_model_name, **model_fields)

            def make_endpoint_func(
                endpoint_name: str, FormModel, session: ClientSession
            ):  # Parameterized endpoint

                async def tool(form_data: FormModel):
                    args = form_data.model_dump(exclude_none=True)
                    print(f"Calling endpoint: {endpoint_name}, with args: {args}")

                    result = await session.call_tool(endpoint_name, arguments=args)
                    return process_tool_response(result)

                return tool

            tool_handler = make_endpoint_func(endpoint_name, FormModel, session)
        else:

            def make_endpoint_func_no_args(
                endpoint_name: str, session: ClientSession
            ):  # Parameterless endpoint
                async def tool():  # No parameters
                    print(f"Calling endpoint: {endpoint_name}, with no args")
                    result = await session.call_tool(
                        endpoint_name, arguments={}
                    )  # Empty dict
                    return process_tool_response(result)  # Same processor

                return tool

            tool_handler = make_endpoint_func_no_args(endpoint_name, session)

        app.post(
            f"/{endpoint_name}",
            summary=endpoint_name.replace("_", " ").title(),
            description=endpoint_description,
            dependencies=[Depends(api_dependency)] if api_dependency else [],
        )(tool_handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    command = getattr(app.state, "command", None)
    args = getattr(app.state, "args", [])
    env = getattr(app.state, "env", {})

    api_dependency = getattr(app.state, "api_dependency", None)

    if not command:
        async with AsyncExitStack() as stack:
            for route in app.routes:
                if isinstance(route, Mount) and isinstance(route.app, FastAPI):
                    await stack.enter_async_context(
                        route.app.router.lifespan_context(route.app),  # noqa
                    )
            yield

    else:
        server_params = StdioServerParameters(
            command=command,
            args=args,
            env={**env},
        )

        async with stdio_client(server_params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                app.state.session = session
                await create_dynamic_endpoints(app, api_dependency=api_dependency)
                yield


async def run(
    host: str = "127.0.0.1",
    port: int = 8000,
    api_key: Optional[str] = "",
    cors_allow_origins=["*"],
    **kwargs,
):
    # Server API Key
    api_dependency = get_verify_api_key(api_key) if api_key else None

    # MCP Config
    config_path = kwargs.get("config")
    server_command = kwargs.get("server_command")
    name = kwargs.get("name") or "MCP OpenAPI Proxy"
    description = (
        kwargs.get("description") or "Automatically generated API from MCP Tool Schemas"
    )
    version = kwargs.get("version") or "1.0"
    ssl_certfile = kwargs.get("ssl_certfile")
    ssl_keyfile = kwargs.get("ssl_keyfile")
    path_prefix = kwargs.get("path_prefix") or "/"

    main_app = FastAPI(
        title=name,
        description=description,
        version=version,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
        lifespan=lifespan,
    )

    main_app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allow_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if server_command:

        main_app.state.command = server_command[0]
        main_app.state.args = server_command[1:]
        main_app.state.env = os.environ.copy()

        main_app.state.api_dependency = api_dependency
    elif config_path:
        with open(config_path, "r") as f:
            config_data = json.load(f)
        mcp_servers = config_data.get("mcpServers", {})
        if not mcp_servers:
            raise ValueError("No 'mcpServers' found in config file.")
        main_app.description += "\n\n- **available tools**："
        for server_name, server_cfg in mcp_servers.items():
            sub_app = FastAPI(
                title=f"{server_name}",
                description=f"{server_name} MCP Server\n\n- [back to tool list](/docs)",
                version="1.0",
                lifespan=lifespan,
            )

            sub_app.add_middleware(
                CORSMiddleware,
                allow_origins=cors_allow_origins or ["*"],
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )

            sub_app.state.command = server_cfg["command"]
            sub_app.state.args = server_cfg.get("args", [])
            sub_app.state.env = {**os.environ, **server_cfg.get("env", {})}

            sub_app.state.api_dependency = api_dependency
            main_app.mount(f"{path_prefix}{server_name}", sub_app)
            main_app.description += f"\n    - [{server_name}](/{server_name}/docs)"
    else:
        raise ValueError("You must provide either server_command or config.")

    config = uvicorn.Config(
        app=main_app,
        host=host,
        port=port,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
        log_level="info",
    )
    server = uvicorn.Server(config)

    await server.serve()
