#!/usr/bin/env python3
import json, base64, yaml
OUT = "deploy/azuredeploy.json"

ENV_HEAD = ("#cloud-config\npackage_update: true\npackage_upgrade: false\npackages:\n"
"  - ffmpeg\n  - python3\n  - python3-pip\n  - python3-venv\n  - curl\n"
"write_files:\n  - path: /etc/e2t/e2t.env\n    permissions: '0600'\n    owner: root:root\n"
"    content: |\n      E2T_PROVIDER=azure\n      E2T_BLOB_ACCOUNT_URL=")
ENV_QUEUE_URL = "\n      E2T_QUEUE_ACCOUNT_URL="
ENV_CONTAINER = "\n      E2T_BLOB_CONTAINER="
ENV_QUEUE = "\n      E2T_QUEUE_NAME="
ENV_NOTIFY_URL = "\n      E2T_NOTIFY_URL="
ENV_NOTIFY_SECRET = "\n      E2T_NOTIFY_SECRET="
ENV_WORKER_ID = "\n      E2T_WORKER_ID="
ENV_MID = "\n      E2T_VISIBILITY=900\n      E2T_HEARTBEAT=120\n      E2T_MAX_DEQUEUE=3\n      E2T_IDLE_EXIT_AFTER="
ENV_SELFDEALLOC = "\n      E2T_SELF_DEALLOCATE="
BODY = ("\n      E2T_WORK_DIR=/var/lib/e2t/work\n"
"  - path: /opt/e2t/run.sh\n    permissions: '0755'\n    owner: root:root\n    content: |\n"
"      #!/bin/bash\n      set -a; source /etc/e2t/e2t.env 2>/dev/null; set +a\n"
"      /opt/e2t/venv/bin/python /opt/e2t/external_transcode_worker.py\n      rc=$?\n"
"      if [ \"${E2T_SELF_DEALLOCATE}\" = \"1\" ] && [ \"$rc\" -eq 0 ]; then\n"
"        rid=$(curl -s -H Metadata:true \"http://169.254.169.254/metadata/instance/compute/resourceId?api-version=2021-02-01&format=text\")\n"
"        az login --identity >/dev/null 2>&1 && az vm deallocate --ids \"$rid\" --no-wait || echo \"self-deallocate failed\"\n"
"      fi\n      exit $rc\n"
"  - path: /etc/systemd/system/e2t-worker.service\n    permissions: '0644'\n    owner: root:root\n    content: |\n"
"      [Unit]\n      Description=e2proxy external transcoding worker\n"
"      After=network-online.target\n      Wants=network-online.target\n"
"      [Service]\n      Type=simple\n      EnvironmentFile=/etc/e2t/e2t.env\n"
"      ExecStart=/opt/e2t/run.sh\n      Restart=on-failure\n      RestartSec=15\n      User=root\n"
"      [Install]\n      WantedBy=multi-user.target\n"
"runcmd:\n  - mkdir -p /opt/e2t /var/lib/e2t/work\n  - python3 -m venv /opt/e2t/venv\n"
"  - /opt/e2t/venv/bin/pip install --upgrade pip\n"
"  - /opt/e2t/venv/bin/pip install azure-identity azure-storage-blob azure-storage-queue\n"
"  - curl -fsSL \"")
TAIL = ("\" -o /opt/e2t/external_transcode_worker.py\n"
"  - curl -sL https://aka.ms/InstallAzureCLIDeb | bash || true\n"
"  - systemctl daemon-reload\n  - systemctl enable --now e2t-worker.service\n")

def lit(s): return "'" + s.replace("'", "''") + "'"
PARTS = [lit(ENV_HEAD),"variables('blobAccountUrl')",lit(ENV_QUEUE_URL),"variables('queueAccountUrl')",
 lit(ENV_CONTAINER),"parameters('containerName')",lit(ENV_QUEUE),"parameters('queueName')",
 lit(ENV_NOTIFY_URL),"parameters('notifyUrl')",lit(ENV_NOTIFY_SECRET),"parameters('notifySecret')",
 lit(ENV_WORKER_ID),"parameters('vmName')",lit(ENV_MID),"string(parameters('idleExitAfter'))",
 lit(ENV_SELFDEALLOC),"variables('selfDeallocateFlag')",lit(BODY),"parameters('workerScriptUrl')",lit(TAIL)]
CUSTOM_DATA = "[base64(concat(" + ", ".join(PARTS) + "))]"

BLOB="ba92f5b4-2d11-453d-a403-e96b0029c9fe"; QUEUE="974c5e8b-45b9-4653-ba55-5f855dd0fb88"; VMR="9980e02c-c2be-4d73-94e8-173b1dc7cf3c"
def pref(p): return "[reference(resourceId('Microsoft.Compute/virtualMachines', parameters('vmName')), '2023-03-01', 'Full').identity.principalId]"
PRIN = "[reference(resourceId('Microsoft.Compute/virtualMachines', parameters('vmName')), '2023-03-01', 'Full').identity.principalId]"

t = {
 "$schema":"https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
 "contentVersion":"1.0.0.0",
 "metadata":{"description":"e2proxy external transcoding worker: storage (blob+queue), network and an on-demand Ubuntu VM that auto-installs ffmpeg + the worker via cloud-init. The VM uses a managed identity for storage access; e2proxy receives ready-to-paste SAS URLs as outputs."},
 "parameters":{
  "vmName":{"type":"string","defaultValue":"e2t-worker","metadata":{"description":"Name of the worker VM."}},
  "vmSize":{"type":"string","defaultValue":"Standard_D4s_v3","metadata":{"description":"VM size. Standard_F4s_v2 or Standard_D4s_v3 are good ffmpeg encoders; bigger = faster."}},
  "adminUsername":{"type":"string","metadata":{"description":"Admin username for the VM."}},
  "authenticationType":{"type":"string","defaultValue":"sshPublicKey","allowedValues":["sshPublicKey","password"],"metadata":{"description":"SSH public key (recommended) or password."}},
  "adminPasswordOrKey":{"type":"secureString","metadata":{"description":"SSH public key string or admin password, per authenticationType."}},
  "storageAccountName":{"type":"string","defaultValue":"[take(concat('e2t', uniqueString(resourceGroup().id)), 24)]","metadata":{"description":"Globally unique storage account name (3-24 lowercase/numbers)."}},
  "containerName":{"type":"string","defaultValue":"transcode","metadata":{"description":"Blob container for job artifacts."}},
  "queueName":{"type":"string","defaultValue":"transcode-jobs","metadata":{"description":"Storage queue for transcode jobs."}},
  "notifyUrl":{"type":"string","defaultValue":"","metadata":{"description":"Webhook the worker calls when a job finishes (e2proxy /api/external-transcode/notify or a Home Assistant webhook). Optional; e2proxy also polls done.json."}},
  "notifySecret":{"type":"secureString","defaultValue":"","metadata":{"description":"Shared HMAC secret for signing notifications. Must match e2proxy config. WARNING: written into VM customData."}},
  "idleExitAfter":{"type":"int","defaultValue":600,"metadata":{"description":"Worker exits after this many seconds with an empty queue. 0 = run forever."}},
  "enableSelfDeallocateOnIdle":{"type":"bool","defaultValue":True,"metadata":{"description":"On clean idle-exit, deallocate the VM (stops compute billing). Grants the VM identity Virtual Machine Contributor on itself."}},
  "workerScriptUrl":{"type":"string","defaultValue":"https://raw.githubusercontent.com/bjoernhoefer/e2proxy/main/workers/external_transcode_worker.py","metadata":{"description":"URL the cloud-init fetches the worker script from. Override if the feature is on a branch or your repo is private."}},
  "sshSourceAddressPrefix":{"type":"string","defaultValue":"*","metadata":{"description":"CIDR/IP allowed to SSH (port 22). Restrict to your IP in production."}},
  "osDiskSizeGB":{"type":"int","defaultValue":128,"metadata":{"description":"OS disk size; needs room for large .ts sources + outputs."}},
  "sasExpiry":{"type":"string","defaultValue":"[dateTimeAdd(utcNow(), 'P1Y')]","metadata":{"description":"Expiry for the SAS URLs returned to e2proxy (default: 1 year)."}},
  "location":{"type":"string","defaultValue":"[resourceGroup().location]","metadata":{"description":"Azure region."}}
 },
 "variables":{
  "vnetName":"[concat(parameters('vmName'), '-vnet')]","subnetName":"default",
  "nsgName":"[concat(parameters('vmName'), '-nsg')]","pipName":"[concat(parameters('vmName'), '-pip')]",
  "nicName":"[concat(parameters('vmName'), '-nic')]",
  "blobAccountUrl":"[concat('https://', parameters('storageAccountName'), '.blob.', environment().suffixes.storage)]",
  "queueAccountUrl":"[concat('https://', parameters('storageAccountName'), '.queue.', environment().suffixes.storage)]",
  "selfDeallocateFlag":"[if(parameters('enableSelfDeallocateOnIdle'), '1', '0')]",
  "subnetId":"[resourceId('Microsoft.Network/virtualNetworks/subnets', variables('vnetName'), variables('subnetName'))]",
  "blobRoleId":"[subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '%s')]"%BLOB,
  "queueRoleId":"[subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '%s')]"%QUEUE,
  "vmRoleId":"[subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '%s')]"%VMR,
  "linuxConfiguration":{"disablePasswordAuthentication":True,"ssh":{"publicKeys":[{"path":"[concat('/home/', parameters('adminUsername'), '/.ssh/authorized_keys')]","keyData":"[parameters('adminPasswordOrKey')]"}]}},
  "sasProperties":{"signedServices":"bq","signedResourceTypes":"sco","signedPermission":"racwdlup","signedProtocol":"https","signedExpiry":"[parameters('sasExpiry')]"}
 },
 "resources":[
  {"type":"Microsoft.Storage/storageAccounts","apiVersion":"2023-01-01","name":"[parameters('storageAccountName')]","location":"[parameters('location')]","sku":{"name":"Standard_LRS"},"kind":"StorageV2","properties":{"minimumTlsVersion":"TLS1_2","allowBlobPublicAccess":False,"supportsHttpsTrafficOnly":True}},
  {"type":"Microsoft.Storage/storageAccounts/blobServices/containers","apiVersion":"2023-01-01","name":"[concat(parameters('storageAccountName'), '/default/', parameters('containerName'))]","dependsOn":["[resourceId('Microsoft.Storage/storageAccounts', parameters('storageAccountName'))]"],"properties":{"publicAccess":"None"}},
  {"type":"Microsoft.Storage/storageAccounts/queueServices/queues","apiVersion":"2023-01-01","name":"[concat(parameters('storageAccountName'), '/default/', parameters('queueName'))]","dependsOn":["[resourceId('Microsoft.Storage/storageAccounts', parameters('storageAccountName'))]"],"properties":{}},
  {"type":"Microsoft.Network/networkSecurityGroups","apiVersion":"2023-04-01","name":"[variables('nsgName')]","location":"[parameters('location')]","properties":{"securityRules":[{"name":"AllowSSH","properties":{"priority":1000,"direction":"Inbound","access":"Allow","protocol":"Tcp","sourceAddressPrefix":"[parameters('sshSourceAddressPrefix')]","sourcePortRange":"*","destinationAddressPrefix":"*","destinationPortRange":"22"}}]}},
  {"type":"Microsoft.Network/virtualNetworks","apiVersion":"2023-04-01","name":"[variables('vnetName')]","location":"[parameters('location')]","dependsOn":["[resourceId('Microsoft.Network/networkSecurityGroups', variables('nsgName'))]"],"properties":{"addressSpace":{"addressPrefixes":["10.42.0.0/16"]},"subnets":[{"name":"[variables('subnetName')]","properties":{"addressPrefix":"10.42.0.0/24","networkSecurityGroup":{"id":"[resourceId('Microsoft.Network/networkSecurityGroups', variables('nsgName'))]"}}}]}},
  {"type":"Microsoft.Network/publicIPAddresses","apiVersion":"2023-04-01","name":"[variables('pipName')]","location":"[parameters('location')]","sku":{"name":"Standard"},"properties":{"publicIPAllocationMethod":"Static"}},
  {"type":"Microsoft.Network/networkInterfaces","apiVersion":"2023-04-01","name":"[variables('nicName')]","location":"[parameters('location')]","dependsOn":["[resourceId('Microsoft.Network/virtualNetworks', variables('vnetName'))]","[resourceId('Microsoft.Network/publicIPAddresses', variables('pipName'))]"],"properties":{"ipConfigurations":[{"name":"ipconfig1","properties":{"privateIPAllocationMethod":"Dynamic","subnet":{"id":"[variables('subnetId')]"},"publicIPAddress":{"id":"[resourceId('Microsoft.Network/publicIPAddresses', variables('pipName'))]"}}}]}},
  {"type":"Microsoft.Compute/virtualMachines","apiVersion":"2023-03-01","name":"[parameters('vmName')]","location":"[parameters('location')]","identity":{"type":"SystemAssigned"},"dependsOn":["[resourceId('Microsoft.Network/networkInterfaces', variables('nicName'))]"],"properties":{"hardwareProfile":{"vmSize":"[parameters('vmSize')]"},"osProfile":{"computerName":"[parameters('vmName')]","adminUsername":"[parameters('adminUsername')]","adminPassword":"[if(equals(parameters('authenticationType'), 'password'), parameters('adminPasswordOrKey'), null())]","linuxConfiguration":"[if(equals(parameters('authenticationType'), 'password'), null(), variables('linuxConfiguration'))]","customData":CUSTOM_DATA},"storageProfile":{"imageReference":{"publisher":"Canonical","offer":"0001-com-ubuntu-server-jammy","sku":"22_04-lts-gen2","version":"latest"},"osDisk":{"createOption":"FromImage","diskSizeGB":"[parameters('osDiskSizeGB')]","managedDisk":{"storageAccountType":"Premium_LRS"}}},"networkProfile":{"networkInterfaces":[{"id":"[resourceId('Microsoft.Network/networkInterfaces', variables('nicName'))]"}]}}},
  {"type":"Microsoft.Authorization/roleAssignments","apiVersion":"2022-04-01","name":"[guid(resourceId('Microsoft.Storage/storageAccounts', parameters('storageAccountName')), variables('blobRoleId'), parameters('vmName'))]","scope":"[concat('Microsoft.Storage/storageAccounts/', parameters('storageAccountName'))]","dependsOn":["[resourceId('Microsoft.Compute/virtualMachines', parameters('vmName'))]","[resourceId('Microsoft.Storage/storageAccounts', parameters('storageAccountName'))]"],"properties":{"roleDefinitionId":"[variables('blobRoleId')]","principalId":PRIN,"principalType":"ServicePrincipal"}},
  {"type":"Microsoft.Authorization/roleAssignments","apiVersion":"2022-04-01","name":"[guid(resourceId('Microsoft.Storage/storageAccounts', parameters('storageAccountName')), variables('queueRoleId'), parameters('vmName'))]","scope":"[concat('Microsoft.Storage/storageAccounts/', parameters('storageAccountName'))]","dependsOn":["[resourceId('Microsoft.Compute/virtualMachines', parameters('vmName'))]","[resourceId('Microsoft.Storage/storageAccounts', parameters('storageAccountName'))]"],"properties":{"roleDefinitionId":"[variables('queueRoleId')]","principalId":PRIN,"principalType":"ServicePrincipal"}},
  {"condition":"[parameters('enableSelfDeallocateOnIdle')]","type":"Microsoft.Authorization/roleAssignments","apiVersion":"2022-04-01","name":"[guid(resourceId('Microsoft.Compute/virtualMachines', parameters('vmName')), variables('vmRoleId'))]","scope":"[concat('Microsoft.Compute/virtualMachines/', parameters('vmName'))]","dependsOn":["[resourceId('Microsoft.Compute/virtualMachines', parameters('vmName'))]"],"properties":{"roleDefinitionId":"[variables('vmRoleId')]","principalId":PRIN,"principalType":"ServicePrincipal"}}
 ],
 "outputs":{
  "storageAccountName":{"type":"string","value":"[parameters('storageAccountName')]"},
  "blobEndpoint":{"type":"string","value":"[variables('blobAccountUrl')]"},
  "queueEndpoint":{"type":"string","value":"[variables('queueAccountUrl')]"},
  "containerName":{"type":"string","value":"[parameters('containerName')]"},
  "queueName":{"type":"string","value":"[parameters('queueName')]"},
  "vmName":{"type":"string","value":"[parameters('vmName')]"},
  "publicIpAddress":{"type":"string","value":"[reference(resourceId('Microsoft.Network/publicIPAddresses', variables('pipName'))).ipAddress]"},
  "sshCommand":{"type":"string","value":"[concat('ssh ', parameters('adminUsername'), '@', reference(resourceId('Microsoft.Network/publicIPAddresses', variables('pipName'))).ipAddress)]"},
  "e2proxyBlobSasUrl":{"type":"string","value":"[concat(variables('blobAccountUrl'), '/', parameters('containerName'), '?', listAccountSas(resourceId('Microsoft.Storage/storageAccounts', parameters('storageAccountName')), '2023-01-01', variables('sasProperties')).accountSasToken)]"},
  "e2proxyQueueSasUrl":{"type":"string","value":"[concat(variables('queueAccountUrl'), '/', parameters('queueName'), '?', listAccountSas(resourceId('Microsoft.Storage/storageAccounts', parameters('storageAccountName')), '2023-01-01', variables('sasProperties')).accountSasToken)]"}
 }
}
with open(OUT,"w") as f:
    json.dump(t,f,indent=2); f.write("\n")
print("wrote", OUT)

sample={"b":"https://e2tsample.blob.core.windows.net","q":"https://e2tsample.queue.core.windows.net"}
rec=(ENV_HEAD+sample["b"]+ENV_QUEUE_URL+sample["q"]+ENV_CONTAINER+"transcode"+ENV_QUEUE+"transcode-jobs"
 +ENV_NOTIFY_URL+"https://ha.example.com/api/webhook/abc"+ENV_NOTIFY_SECRET+"s3cr3t"+ENV_WORKER_ID+"e2t-worker"
 +ENV_MID+"600"+ENV_SELFDEALLOC+"1"+BODY+"https://raw.githubusercontent.com/bjoernhoefer/e2proxy/main/workers/external_transcode_worker.py"+TAIL)
doc=yaml.safe_load(rec)
assert doc["packages"][0]=="ffmpeg"
files={f["path"]:f for f in doc["write_files"]}
assert set(["/etc/e2t/e2t.env","/opt/e2t/run.sh","/etc/systemd/system/e2t-worker.service"])<=set(files)
env=files["/etc/e2t/e2t.env"]["content"]
assert "E2T_BLOB_ACCOUNT_URL=https://e2tsample.blob.core.windows.net" in env
assert "E2T_SELF_DEALLOCATE=1" in env and "E2T_IDLE_EXIT_AFTER=600" in env
assert any("external_transcode_worker.py" in c for c in doc["runcmd"])
base64.b64encode(rec.encode())
print("cloud-init YAML valid OK")
