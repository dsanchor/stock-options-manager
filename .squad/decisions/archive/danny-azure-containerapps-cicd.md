# Frozen Contract: Azure Container Apps CI/CD via GitHub Actions

**Date:** 2026-09-06
**Author:** Danny (Lead)
**Status:** FROZEN — implementation-ready
**Target file:** `.github/workflows/docker-publish.yml`

---

## 1. Summary

Extend the existing `docker-publish.yml` workflow to **automatically deploy** the API and frontend Docker images to Azure Container Apps after a push to `main`. Authentication uses passwordless OIDC via `azure/login@v2`. No long-lived credentials are stored.

---

## 2. Workflow Triggers

No change to triggers. Current triggers remain:

```yaml
on:
  push:
    branches: ['**']
  workflow_dispatch:
```

- **Build job** runs on every push (all branches) and `workflow_dispatch`.
- **Deploy job** runs only when `github.ref == 'refs/heads/main'` (conditional on job-level `if`).
- `workflow_dispatch` on `main` triggers both build and deploy (same condition applies).

---

## 3. Job Structure

### 3.1 `build-and-push` (existing — minimal changes)

**Changes:**
- Add `id-token: write` to permissions (required even though this job doesn't login to Azure — it enables the OIDC token for the workflow, and the deploy job inherits the workflow permissions context). **Actually, move `id-token: write` to the deploy job only** — the build job does not need it.
- Keep existing `permissions: { contents: read, packages: write }` unchanged.
- **No other changes** to the build matrix or steps.

### 3.2 `deploy` (new job)

```yaml
deploy:
  if: github.ref == 'refs/heads/main'
  needs: build-and-push
  runs-on: ubuntu-latest
  permissions:
    id-token: write   # OIDC token for azure/login
    contents: read
  environment: production    # optional: enables environment protection rules
  concurrency:
    group: deploy-production
    cancel-in-progress: false   # do NOT cancel running deploys; queue instead
```

**Rationale for `cancel-in-progress: false`:** Prevents out-of-order deployments. A second push to `main` waits for the first deploy to finish, ensuring the latest push always deploys last.

---

## 4. Immutable Image Tag Construction

The existing `docker/metadata-action@v5` with `type=sha` produces tags like `sha-abc1234` (7-char short SHA).

In the **deploy job**, construct the same tag deterministically:

```yaml
env:
  IMAGE_TAG: sha-${{ github.sha }}   # full 40-char SHA
```

**CORRECTION:** `type=sha` in metadata-action defaults to 7-char short SHA. Use:

```yaml
env:
  SHORT_SHA: ${{ github.sha }}    # we need the short version
steps:
  - name: Set image tag
    id: tag
    run: echo "tag=sha-$(echo '${{ github.sha }}' | head -c 7)" >> "$GITHUB_OUTPUT"
```

The deploy steps then reference `${{ steps.tag.outputs.tag }}` for both images:
- `ghcr.io/dsanchor/option-income-lab-api:sha-<7chars>`
- `ghcr.io/dsanchor/option-income-lab-front:sha-<7chars>`

This matches exactly what `docker/metadata-action@v5` with `type=sha` produces. The `latest` tag is **never** used for deployments — immutable sha tags only.

---

## 5. Deploy Steps (exact sequence)

```yaml
steps:
  - name: Compute image tag
    id: tag
    run: echo "tag=sha-$(echo '${{ github.sha }}' | head -c 7)" >> "$GITHUB_OUTPUT"

  - name: Azure Login (OIDC)
    uses: azure/login@v2
    with:
      client-id: ${{ secrets.AZURE_CLIENT_ID }}
      tenant-id: ${{ secrets.AZURE_TENANT_ID }}
      subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}

  - name: Deploy API to Container Apps
    run: |
      az containerapp update \
        --name ca-stock-options-manager-api \
        --resource-group stock-options-manager-rg \
        --image ghcr.io/dsanchor/option-income-lab-api:${{ steps.tag.outputs.tag }}

  - name: Verify API revision is ready
    run: |
      for i in $(seq 1 30); do
        STATE=$(az containerapp show \
          --name ca-stock-options-manager-api \
          --resource-group stock-options-manager-rg \
          --query "properties.latestRevisionFqdn" -o tsv 2>/dev/null || true)
        RUNNING=$(az containerapp show \
          --name ca-stock-options-manager-api \
          --resource-group stock-options-manager-rg \
          --query "properties.runningStatus" -o tsv 2>/dev/null || echo "Unknown")
        PROV=$(az containerapp revision list \
          --name ca-stock-options-manager-api \
          --resource-group stock-options-manager-rg \
          --query "[0].properties.runningState" -o tsv 2>/dev/null || echo "Unknown")
        echo "Attempt $i/30 — latest revision running state: $PROV"
        if [ "$PROV" = "Running" ]; then
          echo "✅ API revision is running"
          break
        elif [ "$PROV" = "Failed" ]; then
          echo "❌ API revision failed"
          exit 1
        fi
        sleep 10
      done
      if [ "$PROV" != "Running" ]; then
        echo "❌ API revision did not become ready within 5 minutes"
        exit 1
      fi

  - name: Deploy Frontend to Container Apps
    run: |
      az containerapp update \
        --name ca-stock-options-manager-front \
        --resource-group stock-options-manager-rg \
        --image ghcr.io/dsanchor/option-income-lab-front:${{ steps.tag.outputs.tag }}

  - name: Verify Frontend revision is ready
    run: |
      for i in $(seq 1 30); do
        PROV=$(az containerapp revision list \
          --name ca-stock-options-manager-front \
          --resource-group stock-options-manager-rg \
          --query "[0].properties.runningState" -o tsv 2>/dev/null || echo "Unknown")
        echo "Attempt $i/30 — latest revision running state: $PROV"
        if [ "$PROV" = "Running" ]; then
          echo "✅ Frontend revision is running"
          break
        elif [ "$PROV" = "Failed" ]; then
          echo "❌ Frontend revision failed"
          exit 1
        fi
        sleep 10
      done
      if [ "$PROV" != "Running" ]; then
        echo "❌ Frontend revision did not become ready within 5 minutes"
        exit 1
      fi

  - name: Azure Logout
    if: always()
    run: az logout
```

---

## 6. Failure Behavior

| Condition | Behavior |
|-----------|----------|
| Either matrix build fails | `deploy` job is skipped (`needs: build-and-push` + `fail-fast: false` means deploy waits for ALL matrix legs) |
| `az containerapp update` fails | Step fails, job fails, subsequent steps skipped |
| Revision does not reach `Running` within 5 min | Step exits 1, job fails |
| Revision enters `Failed` state | Immediate exit 1 |
| Azure login fails | Job fails immediately |

**Note:** The existing build job has `fail-fast: false`. The `needs: build-and-push` dependency means the deploy job starts only when the **entire matrix** completes successfully. If either API or frontend build fails, the deploy job is skipped.

---

## 7. Concurrency

```yaml
concurrency:
  group: deploy-production
  cancel-in-progress: false
```

Applied at the **deploy job level** (not workflow level, to avoid blocking builds on other branches). This ensures:
- Only one deploy runs at a time
- Queued deploys execute in order (no out-of-order deployments)
- Builds on feature branches are unaffected

---

## 8. GitHub Secrets & Variables

### 8.1 Required GitHub Secrets

The `deploy` job runs with `environment: production`. Secrets may be stored at **repository scope** (Settings → Secrets and variables → Actions → Secrets) or at **environment scope** (Settings → Environments → production → Environment secrets). Either scope works; environment secrets take precedence if both exist.

| Secret | Value | Purpose |
|--------|-------|---------|
| `AZURE_CLIENT_ID` | App registration Application (client) ID | OIDC login |
| `AZURE_TENANT_ID` | Microsoft Entra tenant ID | OIDC login |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID | OIDC login |

### 8.2 Hardcoded in Workflow (not secret)

| Value | Location | Rationale |
|-------|----------|-----------|
| `stock-options-manager-rg` | inline in `az` commands | Not sensitive; single environment |
| `ca-stock-options-manager-api` | inline in `az` commands | Not sensitive; single environment |
| `ca-stock-options-manager-front` | inline in `az` commands | Not sensitive; single environment |
| `ghcr.io/dsanchor/option-income-lab-*` | inline in image refs | Already public in workflow |

**Decision:** Resource group and app names are hardcoded rather than GitHub Variables. This is a single-environment deployment; adding variables adds indirection without benefit. If multi-environment support is needed later, extract to variables then.

### 8.3 Explicitly NOT Required

- ❌ `AZURE_CREDENTIALS` (JSON blob — insecure, deprecated pattern)
- ❌ `AZURE_CLIENT_SECRET` (long-lived secret — replaced by OIDC)
- ❌ GHCR pull credentials in the workflow (Container Apps already configured with GHCR pull)

---

## 9. Required Azure Setup (One-Time)

Run these commands once from a terminal with `Owner` or `User Access Administrator` privileges on the resource group.

### 9.1 Create App Registration (Service Principal)

```bash
# Create the app registration
az ad app create --display-name "github-actions-option-income-lab"

# Note the appId from output — this is AZURE_CLIENT_ID
APP_ID=$(az ad app list --display-name "github-actions-option-income-lab" --query "[0].appId" -o tsv)

# Create the service principal
az ad sp create --id "$APP_ID"
```

### 9.2 Add Federated Credential for GitHub Actions OIDC

> **Prerequisite:** A GitHub Environment named **`production`** must exist in the repository. Create it at **Settings → Environments → New environment → `production`** before the first deploy runs.

```bash
az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name": "github-actions-production-env",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:dsanchor/option-income-lab:environment:production",
  "audiences": ["api://AzureADTokenExchange"],
  "description": "GitHub Actions OIDC for option-income-lab production environment"
}'
```

**Subject format:** `repo:dsanchor/option-income-lab:environment:production`
- Only the `deploy` job, which runs with `environment: production`, can authenticate.
- Azure AD performs an exact-match on the OIDC subject; a branch-ref subject (`ref:refs/heads/main`) would not match and would be rejected.

### 9.3 Assign RBAC Role (Least Privilege)

```bash
# Get the service principal object ID
SP_OBJECT_ID=$(az ad sp list --filter "appId eq '$APP_ID'" --query "[0].id" -o tsv)

# Assign Container Apps Contributor at resource-group scope
az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Container Apps Contributor" \
  --scope "/subscriptions/<SUBSCRIPTION_ID>/resourceGroups/stock-options-manager-rg"
```

**Role choice:** `Container Apps Contributor` is sufficient for `az containerapp update` and `az containerapp revision list/show`. It is more restrictive than `Contributor` and follows least-privilege. The broader `Contributor` role is **not** required.

### 9.4 Configure GitHub Secrets

```bash
# Repository-level (recommended for simplicity)
gh secret set AZURE_CLIENT_ID --body "<APP_ID from step 9.1>"
gh secret set AZURE_TENANT_ID --body "<your-tenant-id>"
gh secret set AZURE_SUBSCRIPTION_ID --body "<your-subscription-id>"

# Alternatively, environment-scoped (Settings → Environments → production → Secrets)
gh secret set AZURE_CLIENT_ID --env production --body "<APP_ID from step 9.1>"
gh secret set AZURE_TENANT_ID --env production --body "<your-tenant-id>"
gh secret set AZURE_SUBSCRIPTION_ID --env production --body "<your-subscription-id>"
```

---

## 10. GHCR Pull Credentials Caveat

The workflow does **not** configure GHCR pull credentials on the Container Apps. This relies on the existing GHCR pull configuration already present on both Container Apps.

**Caveat:** If Container Apps are recreated or the GHCR credentials expire/rotate, the `az containerapp update` will succeed but the revision may fail to pull the image. In that case, reconfigure GHCR pull credentials on the Container Apps:

```bash
az containerapp registry set \
  --name <APP_NAME> \
  --resource-group stock-options-manager-rg \
  --server ghcr.io \
  --username <GITHUB_USERNAME> \
  --password <GHCR_PAT>
```

---

## 11. Complete Workflow (Reference — final shape)

```yaml
name: Build and Push Docker Images

on:
  push:
    branches: ['**']
  workflow_dispatch:

env:
  REGISTRY: ghcr.io

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    strategy:
      fail-fast: false
      matrix:
        include:
          - component: api
            context: ./backend
            dockerfile: ./backend/Dockerfile
          - component: front
            context: ./frontend
            dockerfile: ./frontend/Dockerfile

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract metadata (tags, labels)
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ github.repository }}-${{ matrix.component }}
          tags: |
            type=sha
            type=ref,event=branch
            type=raw,value={{branch}}-{{sha}},enable=true
            type=raw,value=latest,enable={{is_default_branch}}

      - name: Build and push ${{ matrix.component }} image
        uses: docker/build-push-action@v6
        with:
          context: ${{ matrix.context }}
          file: ${{ matrix.dockerfile }}
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}

  deploy:
    if: github.ref == 'refs/heads/main'
    needs: build-and-push
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: read
    environment: production
    concurrency:
      group: deploy-production
      cancel-in-progress: false

    steps:
      - name: Compute image tag
        id: tag
        run: echo "tag=sha-$(echo '${{ github.sha }}' | head -c 7)" >> "$GITHUB_OUTPUT"

      - name: Azure Login (OIDC)
        uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}

      - name: Deploy API to Container Apps
        run: |
          az containerapp update \
            --name ca-stock-options-manager-api \
            --resource-group stock-options-manager-rg \
            --image ghcr.io/dsanchor/option-income-lab-api:${{ steps.tag.outputs.tag }}

      - name: Verify API revision is ready
        run: |
          for i in $(seq 1 30); do
            PROV=$(az containerapp revision list \
              --name ca-stock-options-manager-api \
              --resource-group stock-options-manager-rg \
              --query "sort_by([],&properties.createdTime)[-1].properties.runningState" \
              -o tsv 2>/dev/null || echo "Unknown")
            echo "Attempt $i/30 — latest revision running state: $PROV"
            if [ "$PROV" = "Running" ]; then
              echo "✅ API revision is running"
              break
            elif [ "$PROV" = "Failed" ]; then
              echo "❌ API revision failed"
              exit 1
            fi
            sleep 10
          done
          if [ "$PROV" != "Running" ]; then
            echo "❌ API revision did not become ready within 5 minutes"
            exit 1
          fi

      - name: Deploy Frontend to Container Apps
        run: |
          az containerapp update \
            --name ca-stock-options-manager-front \
            --resource-group stock-options-manager-rg \
            --image ghcr.io/dsanchor/option-income-lab-front:${{ steps.tag.outputs.tag }}

      - name: Verify Frontend revision is ready
        run: |
          for i in $(seq 1 30); do
            PROV=$(az containerapp revision list \
              --name ca-stock-options-manager-front \
              --resource-group stock-options-manager-rg \
              --query "sort_by([],&properties.createdTime)[-1].properties.runningState" \
              -o tsv 2>/dev/null || echo "Unknown")
            echo "Attempt $i/30 — latest revision running state: $PROV"
            if [ "$PROV" = "Running" ]; then
              echo "✅ Frontend revision is running"
              break
            elif [ "$PROV" = "Failed" ]; then
              echo "❌ Frontend revision failed"
              exit 1
            fi
            sleep 10
          done
          if [ "$PROV" != "Running" ]; then
            echo "❌ Frontend revision did not become ready within 5 minutes"
            exit 1
          fi

      - name: Azure Logout
        if: always()
        run: az logout
```

---

## 12. Implementation Checklist

- [ ] **Azure setup** (one-time, manual — see §9)
  - [ ] Create app registration + service principal
  - [ ] Add federated credential for `repo:dsanchor/option-income-lab:environment:production`
  - [ ] Ensure GitHub Environment `production` exists (Settings → Environments)
  - [ ] Assign `Container Apps Contributor` role at resource-group scope
  - [ ] Set GitHub secrets: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
- [ ] **Workflow modification** (single file: `.github/workflows/docker-publish.yml`)
  - [ ] Add `deploy` job after `build-and-push`
  - [ ] No changes to existing `build-and-push` job
- [ ] **Verification**
  - [ ] Push to `main` → build succeeds → deploy job runs → both Container Apps updated
  - [ ] Push to feature branch → build runs → deploy job skipped
  - [ ] Failed build → deploy job skipped
  - [ ] Failed revision → deploy job fails with visible error

---

## 13. Decisions & Rationale

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | OIDC via `azure/login@v2`, no client secret | Official recommended auth; no long-lived credentials |
| D2 | `Container Apps Contributor` role, not `Contributor` | Least privilege; sufficient for `containerapp update/revision list` |
| D3 | Federated credential subject `environment:production` | Workflow uses `environment: production`; Azure OIDC exact-matches the subject. A branch-ref subject (`ref:refs/heads/main`) would fail auth. |
| D4 | `cancel-in-progress: false` concurrency | Prevents out-of-order deploys |
| D5 | Hardcoded RG/app names, not GitHub Variables | Single environment; less indirection |
| D6 | `sha-<7char>` tag, never `latest` | Immutable, matches metadata-action output |
| D7 | Deploy both sequentially (API first, then frontend) | Simpler; frontend may depend on API; verify each before proceeding |
| D8 | 5-minute timeout (30 × 10s) per revision | Generous but bounded; fails visibly |
| D9 | No GHCR credential management in workflow | Already configured on Container Apps |
| D10 | `environment: production` on deploy job | Enables optional GitHub environment protection rules |

---

## 14. Implementation History

**Implemented:** 2026-09-06 by Rusty (Agent Dev)

**Files changed:**
- `.github/workflows/docker-publish.yml` — added `deploy` job verbatim from §11; zero changes to `build-and-push`
- `docs/deployment.md` — prepended CI/CD section (§ "Automated CI/CD") covering: job table, required secrets, optional variables, full OIDC one-time setup commands, federated subject string, least-privilege role + scope, GHCR pull caveat

**Validation:** YAML parsed cleanly by PyYAML; 21/21 contract requirements verified via Python string checks.

**Lessons:**
- Job-level concurrency (`cancel-in-progress: false`) is the right scope; workflow-level would block builds on other branches.
- JMESPath `sort_by([],&properties.createdTime)[-1]` is more robust than `[0]` for revision list ordering — Azure CLI does not guarantee chronological order.
- `type=sha` in `docker/metadata-action@v5` uses 7-char short SHA by default; replicate with `head -c 7` in the deploy job (no actions dep needed).
- OIDC `id-token: write` goes on the deploy job only; build job keeps `contents: read, packages: write` unchanged.
