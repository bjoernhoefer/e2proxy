# Azure deployment (ARM) — external transcoding worker

This template provisions everything needed to run the e2proxy external
transcoding worker on an **on-demand Azure VM**:

- **Storage account** (StorageV2, `Standard_LRS`) with a blob **container**
  (`transcode`) and a **queue** (`transcode-jobs`) for job artifacts and jobs.
- **Network**: VNet + subnet, NSG (SSH only), static public IP, NIC.
- **Ubuntu 22.04 LTS (Gen2) VM** with a **system-assigned managed identity**.
  `cloud-init` (built into the template's `customData`) installs ffmpeg, Python,
  the Azure SDK and the worker, then runs it as the `e2t-worker` systemd service.
- **Role assignments** so the VM identity can read/write the storage:
  *Storage Blob Data Contributor* + *Storage Queue Data Contributor* (always),
  and *Virtual Machine Contributor* on itself (only when
  `enableSelfDeallocateOnIdle` is `true`, for cost-saving self-shutdown).

The VM authenticates to storage with its managed identity, so **no storage keys
land in `customData`**. e2proxy (running on the Pi, which has no Azure identity)
instead uses **SAS URLs**, which the template returns as **outputs** ready to
paste into e2proxy's config.

## Files

| File | Purpose |
|------|---------|
| `azuredeploy.json` | The ARM template. |
| `azuredeploy.parameters.json` | Example parameters — edit before deploying. |
| `cloud-init.yaml` | Stand-alone reference cloud-init for a **non-Azure** VM or home PC (the ARM template embeds its own generated copy; this file is for manual/SMB setups). |

## Deploy with az CLI

```bash
# 1) create a resource group
az group create -n e2t-rg -l westeurope

# 2) edit deploy/azuredeploy.parameters.json (at minimum: adminPasswordOrKey)

# 3) validate, then deploy
az deployment group validate \
  -g e2t-rg \
  --template-file deploy/azuredeploy.json \
  --parameters @deploy/azuredeploy.parameters.json

az deployment group create \
  -g e2t-rg \
  --template-file deploy/azuredeploy.json \
  --parameters @deploy/azuredeploy.parameters.json
```

## Deploy via the Portal (import)

1. Portal → **Deploy a custom template** → **Build your own template in the editor**.
2. **Load file** → select `deploy/azuredeploy.json` → **Save**.
3. Fill in the parameters (SSH key, etc.) → **Review + create**.

## After deployment: wire up e2proxy

Read the deployment outputs:

```bash
az deployment group show -g e2t-rg -n azuredeploy \
  --query properties.outputs -o json
```

Copy `e2proxyBlobSasUrl` and `e2proxyQueueSasUrl` into e2proxy's
`/data/config.json` under `external_transcode`:

```json
{
  "external_transcode": {
    "enabled": true,
    "provider": "azure",
    "blob_container_sas_url": "<e2proxyBlobSasUrl output>",
    "queue_sas_url": "<e2proxyQueueSasUrl output>",
    "notify_secret": "<same secret you passed as notifySecret>",
    "profile": "balanced"
  }
}
```

`sshCommand` output gives you the SSH line; `publicIpAddress` the VM IP.

## Cost control (self-deallocate)

With `enableSelfDeallocateOnIdle = true` and `idleExitAfter > 0`, the worker
exits cleanly when the queue stays empty for `idleExitAfter` seconds. The
`run.sh` wrapper then calls `az vm deallocate` on the VM **itself** (via the
managed identity + IMDS), stopping compute billing. systemd uses
`Restart=on-failure`, so genuine crashes restart but a clean idle-exit stops the
service. To bring the worker back, just **start** the VM again — cloud-init has
already installed everything, and the service is `enabled`.

Set `idleExitAfter = 0` to keep the worker running forever (no auto-shutdown).

## Notes / manual steps

- `az deployment group validate` and a real deploy require Azure credentials and
  the `az` CLI; they cannot be run in this repo's CI/sandbox.
- The worker script is fetched from `workerScriptUrl` (default: `main` branch on
  GitHub). **If the feature is still on a branch**, override `workerScriptUrl`
  with the branch raw URL, e.g.
  `https://raw.githubusercontent.com/bjoernhoefer/e2proxy/feature/external_rendering/workers/external_transcode_worker.py`,
  or merge the worker to `main` first. The repo must be public (or supply your
  own reachable URL).
- `notifySecret` **is** written into `customData` (base64, not encrypted). Treat
  the VM and its deployment history accordingly. The notify webhook is optional —
  e2proxy also polls `done.json` as source of truth.
- Default VM size `Standard_D4s_v3`; `Standard_F4s_v2` is often a better
  price/encode ratio. OS disk defaults to 128 GB for large `.ts` files.
