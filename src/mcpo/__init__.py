import sys
import asyncio
import typer
from typing_extensions import Annotated
from typing import Optional, List

app = typer.Typer()


@app.command(context_settings={"allow_extra_args": True})
def main(
    host: Annotated[
        Optional[str], typer.Option("--host", "-h", help="Host address")
    ] = "0.0.0.0",
    port: Annotated[
        Optional[int], typer.Option("--port", "-p", help="Port number")
    ] = 8000,
):
    # Find the position of "--"
    if "--" not in sys.argv:
        typer.echo("Usage: mcpo --host 0.0.0.0 --port 8000 -- your_mcp_command")
        raise typer.Exit(1)

    idx = sys.argv.index("--")
    server_command: List[str] = sys.argv[idx + 1 :]

    if not server_command:
        typer.echo("Error: You must specify the MCP server command after '--'")

        return

    from mcpo.main import run

    print(
        f"Starting MCP OpenAPI Proxy on {host}:{port} with command: {' '.join(server_command)}"
    )

    # Run your async run function from mcpo.main
    asyncio.run(run(host, port, server_command))


if __name__ == "__main__":
    app()
