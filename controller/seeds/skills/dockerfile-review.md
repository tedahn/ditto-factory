---
name: Dockerfile Review
description: Use when reviewing or writing Dockerfiles for production readiness
---

# Dockerfile Review

## When to Use
- Reviewing a Dockerfile PR for best practices
- Optimizing Docker image size or build time
- Hardening a container image for production security
- Debugging slow or failing Docker builds

## Instructions

1. **Multi-stage builds**: Separate build dependencies from runtime. Use a builder stage with compilers/dev tools, then copy only the built artifacts to a minimal runtime image (e.g., `alpine`, `distroless`, or `slim` variants).

2. **Layer optimization**:
   - Order Dockerfile instructions from least-frequently to most-frequently changing
   - Copy dependency manifests first (`package.json`, `requirements.txt`), install dependencies, then copy source code
   - Combine related `RUN` commands with `&&` to reduce layers
   - Use `.dockerignore` to exclude `.git`, `node_modules`, test files, docs

3. **Security hardening**:
   - Never run as root: add `RUN addgroup -S app && adduser -S app -G app` and `USER app`
   - Pin base image versions with digest: `FROM node:20-alpine@sha256:abc123...`
   - Do not store secrets in the image (no `ENV SECRET_KEY=...` or `COPY .env`)
   - Remove package manager caches after install: `rm -rf /var/cache/apk/*`
   - Scan images with `trivy` or `grype` before deploying

4. **Health checks**: Add a `HEALTHCHECK` instruction for orchestrators to monitor container health:
   ```dockerfile
   HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
     CMD wget -q --spider http://localhost:8080/health || exit 1
   ```

5. **Build reproducibility**:
   - Pin all dependency versions in lock files
   - Use `COPY --chown=app:app` instead of separate `COPY` + `RUN chown`
   - Set `ENV NODE_ENV=production` or equivalent before installing deps
   - Use `ARG` for build-time configuration, `ENV` for runtime configuration

## Checklist
- [ ] Uses multi-stage build to minimize runtime image size
- [ ] Dependency install layer is cached (manifests copied before source)
- [ ] Runs as non-root user
- [ ] Base image pinned to specific version or digest
- [ ] No secrets or credentials baked into the image
- [ ] `.dockerignore` excludes unnecessary files
- [ ] HEALTHCHECK instruction included
- [ ] Image scanned for known vulnerabilities
