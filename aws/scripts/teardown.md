# Teardown checklist

There is deliberately **no automated delete script**. Deleting cloud resources is irreversible, and a script
that guessed wrongly could remove something that is not part of this project. Run the commands below
yourself, one at a time, after reading what each one removes.

**Before you start:**
- Set `AWS_PROFILE` to the account you deployed into and check it: `aws sts get-caller-identity`.
- Set `REGION=ap-south-1` and `BUCKET=<your bucket>`.
- Every resource this project creates has a `grievance-lens` name prefix (or the `--name-prefix` you chose) and,
  where the service supports tags, the tag `project=grievance-lens` (the bucket is the one you named; Glue databases and log groups are
  identified by name). **Delete only resources that match.**
  List them first:
  ```bash
  aws resourcegroupstaggingapi get-resources --region $REGION --tag-filters Key=project,Values=grievance-lens \
    --query 'ResourceTagMappingList[].ResourceARN'
  ```
- If the bucket, a role or a database existed before you ran `deploy.py`, it is not this project's to delete.

Order matters: stop the things that bill or trigger first, and delete the data last.

## 1. Redshift Serverless (if created): bills for storage while idle

```bash
aws redshift-serverless list-workgroups --region $REGION --query 'workgroups[].workgroupName'
aws redshift-serverless delete-workgroup --workgroup-name grievance-lens-wg --region $REGION
# wait until the workgroup has gone, then:
aws redshift-serverless delete-namespace --namespace-name grievance-lens-ns --region $REGION
# optional, BEFORE deleting the namespace, keep a copy (billed):  --final-snapshot-name grievance-lens-final
aws redshift-serverless list-snapshots --region $REGION   # delete any snapshots you no longer want
```
⚠ Deleting the namespace permanently deletes the warehouse data. It can be rebuilt from S3 with
`refresh_warehouse.py`, as long as the curated data still exists.

## 2. The trigger, then the compute

```bash
# Remove ONLY this project's notification and keep any others: inspect first.
aws s3api get-bucket-notification-configuration --bucket $BUCKET
```
If `grievance-lens-batch-manifest` is the only configuration, run
`aws s3api put-bucket-notification-configuration --bucket $BUCKET --notification-configuration '{}'`.
Otherwise, edit the JSON to remove just that entry and put it back.

```bash
aws lambda delete-function --function-name grievance-lens-batch-trigger --region $REGION
aws logs delete-log-group --log-group-name /aws/lambda/grievance-lens-batch-trigger --region $REGION

aws glue get-job-runs --job-name grievance-lens-etl --region $REGION --max-items 1   # nothing RUNNING
aws glue delete-job --job-name grievance-lens-etl --region $REGION
aws glue delete-crawler --name grievance-lens-schema-audit --region $REGION          # if created
```

## 3. Catalog and Athena

```bash
aws glue delete-database --name grievance_lens --region $REGION                 # deletes its 15 table definitions, not the data
aws glue delete-database --name grievance_lens_raw --region $REGION             # if the benchmark created it
aws glue delete-database --name grievance_lens_crawler_audit --region $REGION   # if created
aws athena delete-work-group --work-group grievance-lens --recursive-delete-option --region $REGION
```

## 4. IAM (global)

Delete each role's inline policy first, then the role:

```bash
for r in grievance-lens-glue-etl grievance-lens-lambda-orchestrator grievance-lens-redshift-copy; do
  aws iam list-role-policies --role-name $r          # expect: <role>-policy
  aws iam delete-role-policy --role-name $r --policy-name $r-policy
  aws iam delete-role --role-name $r
done
```
Leave any operator user, group or permission set alone if you created it yourself for other work. If you
made the `operator_policy.json` permission set only for this project, remove it in IAM Identity Center.

## 5. Budget (if created)

```bash
aws budgets delete-budget --account-id <account-id> --budget-name grievance-lens-monthly
```
It costs nothing. Keeping it as a general spending alert is reasonable.

## 6. The data: last, and irreversible

⚠ **This permanently deletes every raw export batch, every curated Parquet file and all quality reports.**
With versioning enabled, every object version must go too, or the bucket keeps billing and cannot be
deleted. The local exports in `data/processed/powerbi/` are unaffected and can be re-uploaded.

```bash
aws s3 ls s3://$BUCKET/            # confirm this is the project bucket: raw/ curated/ control/ athena-results/ scripts/
aws s3 rm s3://$BUCKET --recursive # current versions
# remaining versions and delete markers: easiest in the console (Empty bucket), or:
aws s3api list-object-versions --bucket $BUCKET --query '{Objects: [Versions, DeleteMarkers][][].{Key: Key, VersionId: VersionId}}' --output json > versions.json
aws s3api delete-objects --bucket $BUCKET --delete file://versions.json   # max 1000 per call; repeat if needed
aws s3api delete-bucket --bucket $BUCKET --region $REGION
```

## 7. Check nothing is left

```bash
aws resourcegroupstaggingapi get-resources --region $REGION --tag-filters Key=project,Values=grievance-lens
aws glue get-jobs --region $REGION --query 'Jobs[?starts_with(Name, `grievance-lens`)].Name'
aws lambda list-functions --region $REGION --query 'Functions[?starts_with(FunctionName, `grievance-lens`)].FunctionName'
aws iam list-roles --query 'Roles[?starts_with(RoleName, `grievance-lens`)].RoleName'
```
Then look at Billing → Bills over the next day to confirm that nothing is still accruing charges.
