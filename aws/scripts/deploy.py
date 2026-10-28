"""Create or update the Grievance Lens AWS resources. Plan by default; changes only with --apply.

    python aws/scripts/deploy.py --bucket <globally-unique-name>                    # show the plan
    python aws/scripts/deploy.py --bucket <name> --apply                            # core pipeline
    python aws/scripts/deploy.py --bucket <name> --apply --with-crawler             # + schema-audit crawler
    python aws/scripts/deploy.py --bucket <name> --apply --with-redshift --redshift-max-rpu-hours-per-day 1
    python aws/scripts/deploy.py --bucket <name> --apply --budget-email you@example.com

Core pipeline (all idempotent: existing resources are updated, nothing is deleted):
    S3 bucket       private (all public access blocked), ACLs disabled, SSE-S3 default encryption, TLS-only
                    bucket policy, versioning (raw batches cannot be silently overwritten), lifecycle rules
                    (athena-results/ after 30 days, noncurrent versions after 90, incomplete uploads after 7)
    Artifacts       scripts/etl_job.py and scripts/contract.json
    IAM roles       glue ETL, Lambda orchestrator, Redshift COPY (least-privilege inline policies from
                    aws/iam/policy_templates; placeholders like ${BUCKET} are rendered, AWS variables kept)
    Glue job        Glue 5.0, G.1X x 2 workers (the minimum for Spark), 15 min timeout, 0 retries,
                    MaxConcurrentRuns 1, FLEX execution class by default (cheaper, may start later)
    Lambda          python3.12, 128 MB, 60 s, 2 async retries, 30-day log retention, packaged with boto3
                    (S3 conditional writes need botocore >= 1.35)
    S3 notification ObjectCreated on raw/powerbi/ ... _manifest.json -> Lambda (merged with any existing
                    notification configuration; other notifications are preserved)
    Athena          workgroup with enforced result location s3://<bucket>/athena-results/, SSE-S3 results,
                    engine v3 and a 1 GB scanned-bytes cutoff per query
Opt-in (billable or account-level):
    --with-crawler   Glue crawler for the schema-drift audit (aws/catalog/crawler_config.md)
    --with-redshift  Redshift Serverless namespace + workgroup (8 RPU base, not public) and a usage limit
                     that DEACTIVATES the workgroup after --redshift-max-rpu-hours-per-day
    --budget-email   AWS Budget of --budget-usd per month with 80%/100% e-mail alerts (alerts do not stop spend)
Nothing here enables account-wide settings, changes shared log groups, or deletes resources (see teardown.md).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

AWS = Path(__file__).resolve().parents[1]
TEMPLATES = AWS / "iam" / "policy_templates"
_PLACEHOLDER = re.compile(
    r"\$\{([A-Z_]+)\}"
)  # ${BUCKET} etc.; AWS policy variables like ${aws:userid} are kept
_BUCKET = re.compile(r"^(?!xn--)(?!.*\.\.)[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


def render_template(name: str, values: dict[str, str]) -> dict[str, Any]:
    text = (TEMPLATES / name).read_text(encoding="utf-8")

    def sub(m: re.Match) -> str:
        if m.group(1) not in values:
            raise KeyError(f"{name}: no value for placeholder ${{{m.group(1)}}}")
        return values[m.group(1)]

    return json.loads(_PLACEHOLDER.sub(sub, text))


class Config:
    def __init__(self, a: argparse.Namespace, account_id: str):
        self.a = a
        self.bucket, self.region, self.account = a.bucket, a.region, account_id
        self.prefix = a.name_prefix
        self.glue_job = f"{self.prefix}-etl"
        self.lambda_fn = f"{self.prefix}-batch-trigger"
        self.glue_role = f"{self.prefix}-glue-etl"
        self.lambda_role = f"{self.prefix}-lambda-orchestrator"
        self.redshift_role = f"{self.prefix}-redshift-copy"
        self.athena_workgroup = self.prefix
        self.glue_database = a.glue_database
        self.crawler = f"{self.prefix}-schema-audit"
        self.redshift_namespace = f"{self.prefix}-ns"
        self.redshift_workgroup = f"{self.prefix}-wg"

    @property
    def values(self) -> dict[str, str]:
        return {
            "BUCKET": self.bucket,
            "REGION": self.region,
            "ACCOUNT_ID": self.account,
            "GLUE_JOB_NAME": self.glue_job,
            "LAMBDA_FUNCTION_NAME": self.lambda_fn,
            "GLUE_DATABASE": self.glue_database,
            "ATHENA_WORKGROUP": self.athena_workgroup,
        }

    def role_arn(self, name: str) -> str:
        return f"arn:aws:iam::{self.account}:role/{name}"


# ------------------------------------------------------------------------------- desired-state builders
# Pure functions: what each resource should look like. Unit-tested without AWS (aws/tests/test_deploy.py).


def bucket_lifecycle() -> dict[str, Any]:
    return {
        "Rules": [
            {
                "ID": "expire-athena-results",
                "Status": "Enabled",
                "Filter": {"Prefix": "athena-results/"},
                "Expiration": {"Days": 30},
            },
            {
                "ID": "expire-noncurrent-versions",
                "Status": "Enabled",
                "Filter": {},
                "NoncurrentVersionExpiration": {"NoncurrentDays": 90},
            },
            {
                "ID": "abort-incomplete-uploads",
                "Status": "Enabled",
                "Filter": {},
                "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
            },
        ]
    }


def glue_job_definition(cfg: Config) -> dict[str, Any]:
    return {
        "Role": cfg.role_arn(cfg.glue_role),
        "Command": {
            "Name": "glueetl",
            "ScriptLocation": f"s3://{cfg.bucket}/scripts/etl_job.py",
            "PythonVersion": "3",
        },
        "GlueVersion": "5.0",
        "WorkerType": "G.1X",
        "NumberOfWorkers": 2,
        "Timeout": 15,
        "MaxRetries": 0,
        "ExecutionProperty": {"MaxConcurrentRuns": 1},
        "ExecutionClass": cfg.a.glue_execution_class,
        "DefaultArguments": {
            "--job-language": "python",
            "--TempDir": f"s3://{cfg.bucket}/scripts/glue-temp/",
            "--enable-continuous-cloudwatch-log": "true",
            "--enable-metrics": "true",
            "--job-bookmark-option": "job-bookmark-disable",  # batches are manifest-driven, not bookmarked
            "--glue_database": cfg.glue_database,
            "--contract_uri": f"s3://{cfg.bucket}/scripts/contract.json",
            "--curated_root": f"s3://{cfg.bucket}/curated/",
            "--control_root": f"s3://{cfg.bucket}/control/",
            "--aws_region": cfg.region,
        },
        "Description": "Grievance Lens: raw Power BI CSV batch -> validated partitioned Parquet + catalog publish",
        "Tags": {"project": "grievance-lens"},
    }


def lambda_environment(cfg: Config) -> dict[str, str]:
    return {
        "BUCKET": cfg.bucket,
        "GLUE_JOB_NAME": cfg.glue_job,
        "GLUE_DATABASE": cfg.glue_database,
        "CONTRACT_KEY": "scripts/contract.json",
        "CONTROL_PREFIX": "control/",
        "CURATED_PREFIX": "curated/",
        "MAX_REJECT_RATIO": cfg.a.max_reject_ratio,
        "LOG_LEVEL": "INFO",
    }


NOTIFICATION_ID = "grievance-lens-batch-manifest"


def merged_notification(existing: dict[str, Any], lambda_arn: str) -> dict[str, Any]:
    """put_bucket_notification_configuration REPLACES the whole configuration, so merge instead."""
    out = {
        k: v
        for k, v in existing.items()
        if k
        in (
            "TopicConfigurations",
            "QueueConfigurations",
            "LambdaFunctionConfigurations",
            "EventBridgeConfiguration",
        )
    }
    lambdas = [c for c in out.get("LambdaFunctionConfigurations", []) if c.get("Id") != NOTIFICATION_ID]
    lambdas.append(
        {
            "Id": NOTIFICATION_ID,
            "LambdaFunctionArn": lambda_arn,
            "Events": ["s3:ObjectCreated:*"],
            "Filter": {
                "Key": {
                    "FilterRules": [
                        {"Name": "prefix", "Value": "raw/powerbi/"},
                        {"Name": "suffix", "Value": "_manifest.json"},
                    ]
                }
            },
        }
    )
    out["LambdaFunctionConfigurations"] = lambdas
    return out


def athena_workgroup_configuration(cfg: Config) -> dict[str, Any]:
    return {
        "ResultConfiguration": {
            "OutputLocation": f"s3://{cfg.bucket}/athena-results/",
            "EncryptionConfiguration": {"EncryptionOption": "SSE_S3"},
        },
        "EnforceWorkGroupConfiguration": True,
        "PublishCloudWatchMetricsEnabled": True,
        "BytesScannedCutoffPerQuery": 1_073_741_824,  # 1 GB: a runaway query is cancelled, not billed for TBs
        "RequesterPaysEnabled": False,
        "EngineVersion": {"SelectedEngineVersion": "Athena engine version 3"},
    }


def build_lambda_zip() -> bytes:
    """handler.py plus pinned boto3/botocore (requirements.txt), as an in-memory zip."""
    src = AWS / "lambda"
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--target",
                tmp,
                "-r",
                str(src / "requirements.txt"),
                "--platform",
                "manylinux2014_x86_64",
                "--only-binary=:all:",
                "--python-version",
                "3.12",
                "--implementation",
                "cp",
            ],
            check=True,
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(src / "handler.py", "handler.py")
            for p in Path(tmp).rglob("*"):
                if p.is_file() and "__pycache__" not in p.parts:
                    z.write(p, p.relative_to(tmp).as_posix())
        return buf.getvalue()


# ------------------------------------------------------------------------------------------- the deployer


class Deployer:
    def __init__(self, cfg: Config, apply: bool, session=None):
        self.cfg, self.apply = cfg, apply
        self.session = session
        self.actions: list[str] = []

    def client(self, name: str):
        return self.session.client(name, region_name=self.cfg.region)

    def step(self, description: str, fn=None):
        self.actions.append(description)
        print(("APPLY  " if self.apply else "PLAN   ") + description)
        if self.apply and fn is not None:
            return fn()
        return None

    # S3 ---------------------------------------------------------------------------------------------
    def bucket(self):
        c, b = self.client("s3") if self.apply else None, self.cfg.bucket

        def create():
            try:
                c.head_bucket(Bucket=b)
            except c.exceptions.ClientError:
                kw = {"Bucket": b, "ObjectOwnership": "BucketOwnerEnforced"}
                if self.cfg.region != "us-east-1":
                    kw["CreateBucketConfiguration"] = {"LocationConstraint": self.cfg.region}
                c.create_bucket(**kw)

        self.step(f"s3: bucket {b} in {self.cfg.region} (create if missing; ACLs disabled)", create)
        self.step(
            "s3: block all public access",
            lambda: c.put_public_access_block(
                Bucket=b,
                PublicAccessBlockConfiguration={
                    k: True
                    for k in (
                        "BlockPublicAcls",
                        "IgnorePublicAcls",
                        "BlockPublicPolicy",
                        "RestrictPublicBuckets",
                    )
                },
            ),
        )
        self.step(
            "s3: object ownership BucketOwnerEnforced",
            lambda: c.put_bucket_ownership_controls(
                Bucket=b, OwnershipControls={"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}
            ),
        )
        self.step(
            "s3: default encryption SSE-S3 (AES-256)",
            lambda: c.put_bucket_encryption(
                Bucket=b,
                ServerSideEncryptionConfiguration={
                    "Rules": [
                        {
                            "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
                            "BucketKeyEnabled": True,
                        }
                    ]
                },
            ),
        )
        self.step(
            "s3: versioning enabled",
            lambda: c.put_bucket_versioning(Bucket=b, VersioningConfiguration={"Status": "Enabled"}),
        )
        self.step(
            "s3: lifecycle (athena-results 30 d, noncurrent 90 d, multipart 7 d)",
            lambda: c.put_bucket_lifecycle_configuration(Bucket=b, LifecycleConfiguration=bucket_lifecycle()),
        )
        self.step(
            "s3: bucket policy (deny non-TLS access)",
            lambda: c.put_bucket_policy(
                Bucket=b, Policy=json.dumps(render_template("bucket_policy.json", self.cfg.values))
            ),
        )
        self.step(
            "s3: upload scripts/etl_job.py and scripts/contract.json",
            lambda: [
                c.upload_file(str(AWS / "glue" / "etl_job.py"), b, "scripts/etl_job.py"),
                c.upload_file(str(AWS / "contract" / "contract.json"), b, "scripts/contract.json"),
            ],
        )

    # IAM --------------------------------------------------------------------------------------------
    def role(self, name: str, trust: str, policy: str):
        iam = self.client("iam") if self.apply else None

        def ensure():
            doc = json.dumps(render_template(trust, self.cfg.values))
            try:
                iam.get_role(RoleName=name)
                iam.update_assume_role_policy(RoleName=name, PolicyDocument=doc)
            except iam.exceptions.NoSuchEntityException:
                iam.create_role(
                    RoleName=name,
                    AssumeRolePolicyDocument=doc,
                    Tags=[{"Key": "project", "Value": "grievance-lens"}],
                    Description="Grievance Lens pipeline (aws/iam/policy_templates)",
                )
            iam.put_role_policy(
                RoleName=name,
                PolicyName=f"{name}-policy",
                PolicyDocument=json.dumps(render_template(policy, self.cfg.values)),
            )

        self.step(f"iam: role {name} ({trust} + inline {policy})", ensure)

    # Glue -------------------------------------------------------------------------------------------
    def glue_job(self):
        glue = self.client("glue") if self.apply else None
        job = glue_job_definition(self.cfg)

        def ensure():
            tags = job.pop("Tags")
            try:
                glue.get_job(JobName=self.cfg.glue_job)
                glue.update_job(JobName=self.cfg.glue_job, JobUpdate=job)
            except glue.exceptions.EntityNotFoundException:
                glue.create_job(Name=self.cfg.glue_job, Tags=tags, **job)

        self.step(
            f"glue: job {self.cfg.glue_job} (Glue 5.0, G.1X x2, {self.cfg.a.glue_execution_class}, "
            "timeout 15 min, MaxConcurrentRuns 1)",
            ensure,
        )

    def crawler(self):
        glue = self.client("glue") if self.apply else None
        audit_db = f"{self.cfg.glue_database}_crawler_audit"

        def ensure():
            spec = {
                "Role": self.cfg.role_arn(self.cfg.glue_role),
                "DatabaseName": audit_db,
                "Targets": {"S3Targets": [{"Path": f"s3://{self.cfg.bucket}/curated/"}]},
                "SchemaChangePolicy": {"UpdateBehavior": "LOG", "DeleteBehavior": "LOG"},
                "RecrawlPolicy": {"RecrawlBehavior": "CRAWL_EVERYTHING"},
                "Configuration": json.dumps(
                    {"Version": 1.0, "Grouping": {"TableGroupingPolicy": "CombineCompatibleSchemas"}}
                ),
                "Description": "Schema-drift audit only; authoritative tables are published by the ETL job",
            }
            try:
                glue.get_crawler(Name=self.cfg.crawler)
                glue.update_crawler(Name=self.cfg.crawler, **spec)
            except glue.exceptions.EntityNotFoundException:
                glue.create_crawler(Name=self.cfg.crawler, Tags={"project": "grievance-lens"}, **spec)

        self.step(
            f"glue: crawler {self.cfg.crawler} -> database {audit_db} (LOG-only schema changes; on demand)",
            ensure,
        )

    # Lambda -----------------------------------------------------------------------------------------
    def lambda_function(self):
        lam = self.client("lambda") if self.apply else None
        logs = self.client("logs") if self.apply else None
        fn, env = self.cfg.lambda_fn, lambda_environment(self.cfg)

        def ensure():
            code = build_lambda_zip()
            try:
                lam.get_function(FunctionName=fn)
                lam.update_function_code(FunctionName=fn, ZipFile=code)
                lam.get_waiter("function_updated_v2").wait(FunctionName=fn)
                lam.update_function_configuration(
                    FunctionName=fn,
                    Environment={"Variables": env},
                    Timeout=60,
                    MemorySize=128,
                    Role=self.cfg.role_arn(self.cfg.lambda_role),
                )
            except lam.exceptions.ResourceNotFoundException:
                for attempt in range(6):  # a new IAM role takes a few seconds to become assumable
                    try:
                        lam.create_function(
                            FunctionName=fn,
                            Runtime="python3.12",
                            Handler="handler.lambda_handler",
                            Role=self.cfg.role_arn(self.cfg.lambda_role),
                            Code={"ZipFile": code},
                            Timeout=60,
                            MemorySize=128,
                            Environment={"Variables": env},
                            Tags={"project": "grievance-lens"},
                            Description="Grievance Lens: start the Glue ETL for a complete batch manifest",
                        )
                        break
                    except lam.exceptions.InvalidParameterValueException:
                        if attempt == 5:
                            raise
                        time.sleep(10)
            lam.get_waiter("function_active_v2").wait(FunctionName=fn)
            lam.put_function_event_invoke_config(
                FunctionName=fn, MaximumRetryAttempts=2, MaximumEventAgeInSeconds=3600
            )
            try:
                logs.create_log_group(logGroupName=f"/aws/lambda/{fn}")
            except logs.exceptions.ResourceAlreadyExistsException:
                pass
            logs.put_retention_policy(logGroupName=f"/aws/lambda/{fn}", retentionInDays=30)
            try:
                lam.add_permission(
                    FunctionName=fn,
                    StatementId="allow-s3-batch-manifest",
                    Action="lambda:InvokeFunction",
                    Principal="s3.amazonaws.com",
                    SourceArn=f"arn:aws:s3:::{self.cfg.bucket}",
                    SourceAccount=self.cfg.account,
                )
            except lam.exceptions.ResourceConflictException:
                pass  # permission already present

        self.step(f"lambda: function {fn} (python3.12, 128 MB, 60 s, 2 async retries, logs 30 days)", ensure)

    def notification(self):
        s3 = self.client("s3") if self.apply else None
        arn = f"arn:aws:lambda:{self.cfg.region}:{self.cfg.account}:function:{self.cfg.lambda_fn}"

        def ensure():
            existing = s3.get_bucket_notification_configuration(Bucket=self.cfg.bucket)
            s3.put_bucket_notification_configuration(
                Bucket=self.cfg.bucket, NotificationConfiguration=merged_notification(existing, arn)
            )

        self.step(
            "s3: notification raw/powerbi/*_manifest.json -> Lambda (existing notifications kept)", ensure
        )

    # Athena -----------------------------------------------------------------------------------------
    def athena(self):
        ath = self.client("athena") if self.apply else None
        conf = athena_workgroup_configuration(self.cfg)

        def ensure():
            try:
                ath.get_work_group(WorkGroup=self.cfg.athena_workgroup)
                update = {k: v for k, v in conf.items() if k != "ResultConfiguration"}
                update["ResultConfigurationUpdates"] = conf["ResultConfiguration"]
                ath.update_work_group(WorkGroup=self.cfg.athena_workgroup, ConfigurationUpdates=update)
            except ath.exceptions.InvalidRequestException:
                ath.create_work_group(
                    Name=self.cfg.athena_workgroup,
                    Configuration=conf,
                    Description="Grievance Lens",
                    Tags=[{"Key": "project", "Value": "grievance-lens"}],
                )

        self.step(
            f"athena: workgroup {self.cfg.athena_workgroup} (results in athena-results/, 1 GB cutoff, engine v3)",
            ensure,
        )

    # Redshift Serverless (opt-in) --------------------------------------------------------------------
    def redshift(self):
        rs = self.client("redshift-serverless") if self.apply else None
        role = self.cfg.role_arn(self.cfg.redshift_role)
        a = self.cfg.a

        def ensure():
            try:
                rs.get_namespace(namespaceName=self.cfg.redshift_namespace)
            except rs.exceptions.ResourceNotFoundException:
                rs.create_namespace(
                    namespaceName=self.cfg.redshift_namespace,
                    dbName="dev",
                    iamRoles=[role],
                    defaultIamRoleArn=role,
                    tags=[{"key": "project", "value": "grievance-lens"}],
                )
            try:
                wg = rs.get_workgroup(workgroupName=self.cfg.redshift_workgroup)["workgroup"]
            except rs.exceptions.ResourceNotFoundException:
                kw = {
                    "workgroupName": self.cfg.redshift_workgroup,
                    "namespaceName": self.cfg.redshift_namespace,
                    "baseCapacity": 8,
                    "publiclyAccessible": False,
                    "tags": [{"key": "project", "value": "grievance-lens"}],
                }
                if a.subnet_ids:
                    kw["subnetIds"] = a.subnet_ids.split(",")
                if a.security_group_ids:
                    kw["securityGroupIds"] = a.security_group_ids.split(",")
                wg = rs.create_workgroup(**kw)["workgroup"]
            existing = rs.list_usage_limits(resourceArn=wg["workgroupArn"], usageType="serverless-compute")
            if not existing.get("usageLimits"):
                rs.create_usage_limit(
                    resourceArn=wg["workgroupArn"],
                    usageType="serverless-compute",
                    amount=int(a.redshift_max_rpu_hours_per_day),
                    period="daily",
                    breachAction="deactivate",
                )

        self.step(
            f"redshift-serverless: namespace {self.cfg.redshift_namespace} + workgroup {self.cfg.redshift_workgroup} "
            f"(8 RPU base, private) + usage limit {a.redshift_max_rpu_hours_per_day} RPU-h/day -> deactivate",
            ensure,
        )

    def budget(self):
        b = self.client("budgets") if self.apply else None
        a = self.cfg.a

        def ensure():
            name = f"{self.cfg.prefix}-monthly"
            try:
                b.describe_budget(AccountId=self.cfg.account, BudgetName=name)
                return
            except b.exceptions.NotFoundException:
                pass
            b.create_budget(
                AccountId=self.cfg.account,
                Budget={
                    "BudgetName": name,
                    "BudgetLimit": {"Amount": str(a.budget_usd), "Unit": "USD"},
                    "TimeUnit": "MONTHLY",
                    "BudgetType": "COST",
                },
                NotificationsWithSubscribers=[
                    {
                        "Notification": {
                            "NotificationType": t,
                            "ComparisonOperator": "GREATER_THAN",
                            "Threshold": pct,
                            "ThresholdType": "PERCENTAGE",
                        },
                        "Subscribers": [{"SubscriptionType": "EMAIL", "Address": a.budget_email}],
                    }
                    for t, pct in (("ACTUAL", 80.0), ("ACTUAL", 100.0), ("FORECASTED", 100.0))
                ],
            )

        self.step(
            f"budgets: {self.cfg.prefix}-monthly ${a.budget_usd}/month, alerts to {a.budget_email} "
            "(an alert, not a spending limit)",
            ensure,
        )

    def run(self):
        self.bucket()
        self.role(self.cfg.glue_role, "trust_glue.json", "glue_etl_policy.json")
        self.role(self.cfg.lambda_role, "trust_lambda.json", "lambda_orchestrator_policy.json")
        self.role(self.cfg.redshift_role, "trust_redshift.json", "redshift_copy_policy.json")
        self.glue_job()
        if self.cfg.a.with_crawler:
            self.crawler()
        self.lambda_function()
        self.notification()
        self.athena()
        if self.cfg.a.with_redshift:
            self.redshift()
        if self.cfg.a.budget_email:
            self.budget()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--bucket", default=os.environ.get("GL_BUCKET"), required=os.environ.get("GL_BUCKET") is None
    )
    p.add_argument("--region", default=os.environ.get("GL_REGION", "ap-south-1"))
    p.add_argument("--name-prefix", default="grievance-lens")
    p.add_argument("--glue-database", default="grievance_lens")
    p.add_argument("--glue-execution-class", choices=["FLEX", "STANDARD"], default="FLEX")
    p.add_argument("--max-reject-ratio", default="0")
    p.add_argument("--apply", action="store_true", help="make the changes (default: print the plan only)")
    p.add_argument("--account-id", help="for the plan only; --apply always uses sts:GetCallerIdentity")
    p.add_argument("--with-crawler", action="store_true")
    p.add_argument("--with-redshift", action="store_true")
    p.add_argument("--redshift-max-rpu-hours-per-day", type=int, default=1)
    p.add_argument(
        "--subnet-ids", help="comma-separated, for the Redshift workgroup (default VPC if omitted)"
    )
    p.add_argument("--security-group-ids")
    p.add_argument("--budget-email")
    p.add_argument("--budget-usd", type=float, default=5.0)
    a = p.parse_args(argv)
    if not _BUCKET.match(a.bucket or ""):
        p.error(
            "--bucket must be a valid S3 bucket name (3-63 chars, lowercase letters, digits, '.' and '-')"
        )
    return a


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    session = None
    account = a.account_id or "123456789012"
    if a.apply:
        import boto3

        session = boto3.session.Session(region_name=a.region)
        account = session.client("sts").get_caller_identity()["Account"]
    cfg = Config(a, account)
    Deployer(cfg, a.apply, session).run()
    if not a.apply:
        print("\nNothing was changed. Re-run with --apply to create or update these resources.")
    else:
        print(
            f"\nDone. Glue job: {cfg.glue_job}; Lambda: {cfg.lambda_fn}; Athena workgroup: {cfg.athena_workgroup}."
        )
        print(f"Next: GL_BUCKET={cfg.bucket} aws/scripts/upload_exports.sh  (or .ps1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
