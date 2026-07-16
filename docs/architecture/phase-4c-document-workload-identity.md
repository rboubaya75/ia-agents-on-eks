# Phase 4C — Document workload identity

## Objective

Implement the AWS workload-identity boundary required by ADR-0011 for the existing document API and ingestion worker.

This sub-lot creates separate least-privilege IAM roles and EKS Pod Identity associations for the two workloads. It does not deploy Kubernetes workloads and does not create or modify the EKS cluster, networking, node groups, the Pod Identity Agent or cluster-level IMDS controls.

## Included scope

- reusable Terraform module `infra/terraform/modules/document-workload-identity`;
- one IAM role for the document API service account;
- one IAM role for the document ingestion worker service account;
- EKS Pod Identity associations for the existing cluster and namespace;
- resource- and prefix-scoped IAM policies derived from the Phase 4B data-plane outputs;
- optional customer-managed KMS permissions constrained to the required encryption contexts;
- typed inputs and outputs for later Helm composition;
- Terraform tests and static policy assertions;
- development-environment composition without committed AWS identifiers.

## Excluded scope

- EKS cluster, node group or Pod Identity Agent creation;
- node firewall, CNI or platform implementation of IMDS isolation;
- Helm deployments and Kubernetes service-account manifests;
- CloudWatch observability resources;
- GitHub OIDC plan and apply roles or workflows;
- real AWS deployment and integration tests;
- frontend, durable document deletion, retrieval and custom agents.

## Trust boundary

Both roles trust only the EKS Pod Identity service principal:

```text
pods.eks.amazonaws.com
```

The trust policy permits only the actions required by EKS Pod Identity and contains no account, cluster, namespace or service-account wildcard supplied as a hardcoded identifier. Runtime binding to cluster, namespace and service account is performed by explicit `aws_eks_pod_identity_association` resources.

The module creates exactly two associations:

```text
<cluster> / <namespace> / ia-agents-api    -> API IAM role
<cluster> / <namespace> / ia-agents-worker -> worker IAM role
```

Service-account names are typed inputs so Helm can consume the same authoritative values later.

## API permission contract

The API role may perform only the operations required to:

- read and conditionally update document, job and submission-lease records in the configured DynamoDB table;
- create presigned temporary uploads through the application S3 adapter;
- inspect temporary-object metadata;
- copy an approved temporary object into the immutable source prefix;
- delete temporary objects after successful promotion;
- send ingestion tasks to the configured FIFO queue;
- perform non-costly readiness checks against the configured resources.

The API role must not be able to:

- invoke Bedrock models or inference profiles;
- receive, delete or change visibility of SQS messages;
- write chunks;
- mutate S3 Vectors indexes;
- administer IAM, EKS, KMS, DynamoDB, S3 or SQS resources.

## Worker permission contract

The worker role may perform only the operations required to:

- receive ingestion messages, renew visibility and acknowledge completed messages;
- read and conditionally update document, job, lease and generation records;
- read immutable source objects;
- write generation-scoped chunk objects and remove only explicitly retired inactive-generation data;
- invoke the configured Bedrock embedding model or inference profile;
- write vectors to configured retained indexes and perform readiness reads.

The worker role must not be able to:

- create temporary upload sessions;
- copy temporary uploads into the immutable source prefix;
- delete temporary uploads;
- send ingestion tasks;
- administer unrelated platform resources.

## Resource scoping

IAM policy resources are composed only from typed module inputs produced by the Phase 4B data plane or externally injected prerequisites:

- DynamoDB table ARN and index ARNs derived from it;
- document bucket ARN plus explicit upload, source and chunk prefixes;
- ingestion queue ARN;
- S3 Vectors bucket and retained index ARNs;
- permitted Bedrock model or inference-profile ARNs;
- optional KMS key ARNs.

Wildcard actions are forbidden. A wildcard resource is allowed only where the AWS API cannot support narrower resource-level permissions, and every such case requires an inline justification plus a regression assertion.

## KMS boundary

When customer-managed KMS keys are configured:

- API permissions cover encryption and decryption only for temporary-upload and immutable-source promotion paths;
- worker permissions cover immutable-source reads and chunk writes;
- grants are scoped through the expected AWS service and encryption-context conditions where supported;
- neither role receives KMS administration permissions.

## IMDS and node-role prerequisite

This module does not claim to enforce cluster-level IMDS isolation. Before enabling the document feature, deployment validation must prove:

- API and worker pods use `hostNetwork: false`;
- application pods cannot obtain the node IAM role through EC2 Instance Metadata Service;
- the EKS Pod Identity credential path remains available;
- each workload resolves its expected role using `sts:GetCallerIdentity`;
- no static AWS credential variables are rendered into Kubernetes manifests.

These checks are completed with the Helm and deployment-validation sub-lots. Phase 4C exposes the role and service-account outputs required by those checks.

## Terraform module layout

```text
infra/terraform/modules/document-workload-identity/
├── versions.tf
├── variables.tf
├── trust.tf
├── api-role.tf
├── worker-role.tf
├── pod-identity.tf
├── outputs.tf
└── tests/
    └── workload_identity.tftest.hcl
```

The development environment composes the module from Phase 4B outputs and externally supplied EKS and Bedrock inputs.

## Required tests

Terraform and static tests must prove at least:

- exactly two distinct IAM roles and Pod Identity associations are created;
- API and worker service-account names cannot be equal;
- cluster name, namespace and service-account names are non-empty and validated;
- API policy contains no Bedrock invocation, SQS receive/delete/visibility or S3 Vectors mutation action;
- worker policy contains no SQS send or temporary-upload promotion permission;
- both policies reject IAM, EKS and broad infrastructure-administration actions;
- DynamoDB, S3, SQS and S3 Vectors resources are derived from explicit inputs;
- Bedrock invocation is limited to configured model or inference-profile ARNs;
- optional KMS permissions contain no administration actions;
- no AWS account ID, role ARN, cluster name, region or resource name is committed as an environment value;
- outputs expose the two role ARNs, association identifiers and service-account names.

## Validation gate

Before the Phase 4C pull request is ready for review:

- `terraform fmt -check` passes;
- module initialization and validation pass without backend access;
- `terraform test` passes;
- development-environment composition validates;
- provider-lock integrity passes;
- static least-privilege and forbidden-action assertions pass;
- existing Ruff, mypy strict and pytest quality gates remain green.

No AWS apply or real integration-test success will be claimed in this sub-lot.

## Exit criteria

Phase 4C is complete only when the module, environment composition, tests and documentation are reviewed and merged. Helm deployment work must not begin before this gate is approved.
