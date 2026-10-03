# LetsFG MCP Server
# For use with Glama.ai and other containerized MCP deployments
#
# The server is a stdio client: it speaks HTTPS to letsfg.co and runs no browser.
# Playwright and its system libraries used to be installed here for local
# connectors that no longer exist.

FROM node:22-slim

WORKDIR /app

# Install letsfg-mcp from npm
RUN npm install -g letsfg-mcp@latest

# Environment variables (optional - search works without API key)
ENV LETSFG_API_KEY=""

# MCP server runs on stdio
ENTRYPOINT ["letsfg-mcp"]
