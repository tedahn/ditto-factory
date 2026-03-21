# images/mock-agent/Dockerfile
FROM alpine:3.20

RUN apk add --no-cache bash git jq redis curl

# Build args wired to env vars so variant images work
ARG MOCK_FAIL_PHASE=""
ARG MOCK_COMMIT_COUNT=""
ARG MOCK_DELAY_SECONDS=""
ENV MOCK_FAIL_PHASE=${MOCK_FAIL_PHASE}
ENV MOCK_COMMIT_COUNT=${MOCK_COMMIT_COUNT}
ENV MOCK_DELAY_SECONDS=${MOCK_DELAY_SECONDS}

RUN adduser -D -u 1000 agent
WORKDIR /home/agent

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER 1000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
