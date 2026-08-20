// Azure infrastructure for the Alliant Revenue Processing Engine.
//
// Provisions:
//   * Azure SQL Database (new database on existing or new SQL Server)
//   * Azure Container App (Streamlit UI)
//
// Shares the same Container Apps environment and SQL Server as
// alliant-purchasing-solutions when deployed to the same resource group.
//
// Deploy:
//   az deployment group create -g alliant-rg -f infra/main.bicep \
//       -p sqlAdminPassword='<password>' appImage='<acr>/revenue-engine:latest'

@description('Base name prefix for resources.')
param namePrefix string = 'rev'

@description('Deployment location.')
param location string = resourceGroup().location

@description('Azure SQL administrator login.')
param sqlAdminLogin string = 'pdnadmin'

@secure()
@description('Azure SQL administrator password.')
param sqlAdminPassword string

@description('Fully-qualified container image reference.')
param appImage string

@description('Container registry server (e.g. myacr.azurecr.io).')
param registryServer string = ''

@description('Container registry username.')
param registryUsername string = ''

@secure()
@description('Container registry password.')
param registryPassword string = ''

@description('Name of an existing Container Apps managed environment to share. Leave empty to create a new one.')
param existingEnvironmentName string = ''

@description('Name of an existing Azure SQL Server to share. Leave empty to create a new one.')
param existingSqlServerName string = ''

@description('CIDR ranges allowed to reach the UI ingress. Empty = public.')
param allowedIpRanges array = []

// --- Azure SQL (reuse existing server or create) ---------------------------

resource existingSqlServer 'Microsoft.Sql/servers@2023-05-01-preview' existing = if (existingSqlServerName != '') {
  name: existingSqlServerName
}

resource newSqlServer 'Microsoft.Sql/servers@2023-05-01-preview' = if (existingSqlServerName == '') {
  name: '${namePrefix}-sql-${uniqueString(resourceGroup().id)}'
  location: location
  properties: {
    administratorLogin: sqlAdminLogin
    administratorLoginPassword: sqlAdminPassword
    minimalTlsVersion: '1.2'
  }
}

resource sqlFirewall 'Microsoft.Sql/servers/firewallRules@2023-05-01-preview' = if (existingSqlServerName == '') {
  parent: newSqlServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// "parent" must be a direct reference to a resource symbol, not a variable
// that conditionally resolves to one of two resources — so the database is
// declared twice, once per branch, each parented directly.
resource revenueDbOnExisting 'Microsoft.Sql/servers/databases@2023-05-01-preview' = if (existingSqlServerName != '') {
  parent: existingSqlServer
  name: 'alliant_revenue'
  location: location
  sku: { name: 'S0', tier: 'Standard' }
}

resource revenueDbOnNew 'Microsoft.Sql/servers/databases@2023-05-01-preview' = if (existingSqlServerName == '') {
  parent: newSqlServer
  name: 'alliant_revenue'
  location: location
  sku: { name: 'S0', tier: 'Standard' }
}

var sqlServerFqdn = existingSqlServerName != '' ? existingSqlServer.properties.fullyQualifiedDomainName : newSqlServer.properties.fullyQualifiedDomainName
var databaseName = existingSqlServerName != '' ? revenueDbOnExisting.name : revenueDbOnNew.name

// --- Container Apps (reuse existing environment or create) ------------------

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = if (existingEnvironmentName == '') {
  name: '${namePrefix}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource existingEnv 'Microsoft.App/managedEnvironments@2024-03-01' existing = if (existingEnvironmentName != '') {
  name: existingEnvironmentName
}

resource newEnv 'Microsoft.App/managedEnvironments@2024-03-01' = if (existingEnvironmentName == '') {
  name: '${namePrefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

var envId = existingEnvironmentName != '' ? existingEnv.id : newEnv.id

// --- Secrets & connection strings ------------------------------------------

var sqlConnString = 'Driver={ODBC Driver 18 for SQL Server};Server=tcp:${sqlServerFqdn},1433;Database=${databaseName};Uid=${sqlAdminLogin};Pwd=${sqlAdminPassword};Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30'

var appSecrets = concat(
  [
    { name: 'sql-conn', value: sqlConnString }
  ],
  registryServer == '' ? [] : [
    { name: 'registry-password', value: registryPassword }
  ]
)

var registries = registryServer == '' ? [] : [
  {
    server: registryServer
    username: registryUsername
    passwordSecretRef: 'registry-password'
  }
]

var ipSecurityRestrictions = [for (cidr, i) in allowedIpRanges: {
  name: 'allow-${i}'
  ipAddressRange: cidr
  action: 'Allow'
}]

// --- Container App ---------------------------------------------------------

resource revenueApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-ui'
  location: location
  properties: {
    managedEnvironmentId: envId
    configuration: {
      ingress: {
        external: true
        targetPort: 8501
        ipSecurityRestrictions: empty(allowedIpRanges) ? null : ipSecurityRestrictions
      }
      secrets: appSecrets
      registries: registries
    }
    template: {
      containers: [
        {
          name: 'revenue-engine'
          image: appImage
          resources: { cpu: json('1'), memory: '2Gi' }
          env: [
            { name: 'AZURE_SQL_CONNECTION_STRING', secretRef: 'sql-conn' }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

output appUrl string = 'https://${revenueApp.properties.configuration.ingress.fqdn}'
output sqlServerFqdn string = sqlServerFqdn
output databaseName string = databaseName
