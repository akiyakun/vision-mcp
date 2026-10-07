FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MCP_PORT=8000
COPY --chmod=644 requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --chmod=644 server.py ./
COPY vision_mcp ./vision_mcp
RUN chmod -R a=rX /app && mkdir /data /images && chown 10001:10001 /data
USER 10001:10001
EXPOSE 8000
CMD ["python", "server.py", "--http"]
