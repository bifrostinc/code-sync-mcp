#!/usr/bin/env python3
import argparse
import asyncio
import json
import os

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

load_dotenv()


async def main():
    if not os.path.exists("server.py"):
        print(
            "Error: server.py not found, are you running this from the bifrost_mcp directory?"
        )
        return

    # Create the main parser
    parser = argparse.ArgumentParser(description="Bifrost Rsync Client")
    parser.add_argument("-f", "--bifrost-file-path", type=str, required=True)
    parser.add_argument("-e", "--env", choices=["dev", "prod"], required=True)
    args = parser.parse_args()

    with open(args.bifrost_file_path, "r") as f:
        bifrost_file = json.load(f)

    if args.env == "dev":
        # Hardcoded for now from local dev setup.
        api_key = "2c8edbd2-c2be-41c8-a1e9-f5c222a9845b"
        api_url = "http://localhost:8000"
        ws_api_url = "ws://localhost:8000"
    else:
        api_key = os.getenv("BIFROST_API_KEY")
        if not api_key:
            raise ValueError(
                "BIFROST_API_KEY must be set for the production environment"
            )
        api_url = os.getenv(
            "BIFROST_API_URL",
            "http://bifrost-backend-alb-610931638.us-east-1.elb.amazonaws.com",
        )
        ws_api_url = os.getenv(
            "BIFROST_WS_API_URL",
            "ws://bifrost-backend-alb-610931638.us-east-1.elb.amazonaws.com",
        )

    stdio_params = StdioServerParameters(
        command="uv",
        args=["run", "server.py"],
        env={
            "BIFROST_API_KEY": api_key,
            "BIFROST_API_URL": api_url,
            "BIFROST_WS_API_URL": ws_api_url,
        },
    )

    shared_args = {
        "app_id": bifrost_file["app_id"],
        "deployment_id": bifrost_file["deployment_id"],
        "app_root": bifrost_file["app_root"],
        "code_diff": "initial",
        "change_description": "initial",
    }

    async with stdio_client(stdio_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            parser = argparse.ArgumentParser(
                description="Bifrost Rsync Client", exit_on_error=False
            )
            result = await session.list_tools()
            subparsers = parser.add_subparsers(
                dest="command", help="Available commands"
            )

            # Create subparsers for each tool
            for tool in result.tools:
                subparser = subparsers.add_parser(
                    tool.name, help=tool.description, exit_on_error=False
                )

                # Add arguments based on the tool's parameters
                for param in tool.inputSchema.get("properties", {}).values():
                    param_name = param.get("title", "").replace(" ", "-").lower()
                    param_type = param.get("type", "string")
                    param_description = param.get("description", "")

                    if param_name and param_name.replace("-", "_") not in shared_args:
                        subparser.add_argument(
                            f"--{param_name}",
                            type=str if param_type == "string" else int,
                            help=param_description,
                            required=True,
                        )

            while True:
                print(" > ", end=" ")
                command = input()
                try:
                    args = parser.parse_args(command.split())
                except argparse.ArgumentError as e:
                    parser.print_help()
                    print(f"Error: {e}")
                    continue

                if args.command is None:
                    parser.print_help()
                    continue
                await session.call_tool(
                    args.command,
                    {**shared_args, **vars(args)},
                )


if __name__ == "__main__":
    asyncio.run(main())
