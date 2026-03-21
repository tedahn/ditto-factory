# Architecture Diagram Changes Needed

## Status: Proposed

## Context
REST API endpoints (`/api/tasks`, `/api/threads`) and the `/ditto` Claude Code skill
were added. The Infrastructure diagram does not reflect these. `DF_API_KEY` auth was
added in `config.py` but is missing from Helm secrets and the diagram.

## Exact Mermaid Changes (Infrastructure Deployment View)

### 1. Add CLI/Skill to External Sources (line 11, after Linear)

```mermaid
        CLI["CLI / Skill<br/>REST API calls"]
```

### 2. Add REST API node inside Controller subgraph (line 15, after Webhooks)

```mermaid
        API["REST API<br/>/api/tasks · /api/threads"]
```

### 3. Add edges (after line 45)

```mermaid
    CLI -->|"REST API"| API
    API --> Registry
```

### 4. Update Secrets node (line 268)

Old:
```mermaid
        Sec["Secrets: df-secrets<br/>anthropic-api-key<br/>slack, github, linear tokens"]
```

New:
```mermaid
        Sec["Secrets: df-secrets<br/>anthropic-api-key · df-api-key<br/>slack, github, linear tokens"]
```

### 5. Update Docker Compose controller label (line 272, optional)

Old:
```mermaid
        DC_Ctrl["Controller<br/>SQLite + Redis"]
```

New:
```mermaid
        DC_Ctrl["Controller (port 8000)<br/>SQLite + Redis"]
```

## Helm Chart Changes Needed (not diagram)

### charts/ditto-factory/templates/secrets.yaml
Add:
```yaml
  df-api-key: {{ .Values.secrets.dfApiKey | b64enc | quote }}
```

### charts/ditto-factory/templates/controller-deployment.yaml
Add env var:
```yaml
            - name: DF_API_KEY
              valueFrom:
                secretKeyRef:
                  name: df-secrets
                  key: df-api-key
```

### charts/ditto-factory/values.yaml
Add:
```yaml
secrets:
  dfApiKey: ""
```

## Networking Note

Both webhooks and the REST API are inbound to the controller on port 8000.
No new port or protocol is needed. However, for CLI/Skill access from outside
the cluster, the Helm chart should ensure an Ingress or LoadBalancer Service
exposes port 8000. Currently the chart templates should be checked for this.
