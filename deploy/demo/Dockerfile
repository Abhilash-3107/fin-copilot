# Hosted demo instance: seeded sample data, uploads blocked at the API layer
# (FC_DEMO_MODE=1), LLM provider "none" (no key to leak, no cost). Deploy = pull
# a newer finance-copilot release and rebuild.
FROM python:3.12-slim

RUN pip install --no-cache-dir finance-copilot

ENV FC_DEMO_MODE=1 \
    FC_DATA_DIR=/srv/data \
    LLM_PROVIDER=none \
    EMBEDDING_PROVIDER=none \
    HOST=0.0.0.0 \
    PORT=8080

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
